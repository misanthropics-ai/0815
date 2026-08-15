"""One-command AWS deploy: ECR image + App Runner service.

  python deploy/deploy_aws.py           # build+push+deploy
  python deploy/deploy_aws.py --status  # check service status/URL

Reads AWS creds from backend/.env (via backend.config). Tries to create an
App Runner *instance role* with Bedrock permissions so the deployed service
does NOT depend on the (expiring) session credentials; falls back to passing
the session creds as env vars if IAM is locked down (refresh them later with
--update-env). Requires local docker.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import config  # noqa: E402  (loads backend/.env into os.environ)

import boto3  # noqa: E402

REGION = config.AWS_REGION
APP = "ai-rec-diagnostics"
ECR_REPO = APP
SERVICE = APP
ECR_ACCESS_ROLE = "AppRunnerECRAccess0815"
INSTANCE_ROLE = "AppRunnerBedrock0815"


def sh(cmd: list[str]) -> None:
    print("+", " ".join(cmd[:6]), "..." if len(cmd) > 6 else "")
    subprocess.run(cmd, check=True)


def ensure_ecr(ecr) -> str:
    try:
        out = ecr.describe_repositories(repositoryNames=[ECR_REPO])
        return out["repositories"][0]["repositoryUri"]
    except ecr.exceptions.RepositoryNotFoundException:
        out = ecr.create_repository(repositoryName=ECR_REPO)
        return out["repository"]["repositoryUri"]


def docker_push(ecr, uri: str) -> str:
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user_pass = base64.b64decode(auth["authorizationToken"]).decode()
    registry = auth["proxyEndpoint"].replace("https://", "")
    password = user_pass.split(":", 1)[1]
    subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", registry],
                   input=password.encode(), check=True)
    root = Path(__file__).resolve().parent.parent
    sh(["docker", "build", "-t", ECR_REPO, "-f", str(root / "backend/Dockerfile"), str(root)])
    tag = f"{uri}:latest"
    sh(["docker", "tag", f"{ECR_REPO}:latest", tag])
    sh(["docker", "push", tag])
    return tag


def ensure_role(iam, name: str, service: str, policy_arn: str | None = None,
                inline: dict | None = None) -> str | None:
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}]}
    try:
        try:
            role = iam.get_role(RoleName=name)["Role"]
        except iam.exceptions.NoSuchEntityException:
            role = iam.create_role(RoleName=name,
                                   AssumeRolePolicyDocument=json.dumps(trust))["Role"]
            time.sleep(8)  # IAM propagation
        if policy_arn:
            iam.attach_role_policy(RoleName=name, PolicyArn=policy_arn)
        if inline:
            iam.put_role_policy(RoleName=name, PolicyName=f"{name}-inline",
                                PolicyDocument=json.dumps(inline))
        return role["Arn"]
    except Exception as e:  # noqa: BLE001
        print(f"  ! IAM role {name} unavailable: {e}")
        return None


def env_vars(use_creds: bool) -> dict:
    env = {"AWS_DEFAULT_REGION": REGION, "MODE": "auto", "PORT": "8000"}
    if config.env("BEDROCK_MODEL"):
        env["BEDROCK_MODEL"] = config.env("BEDROCK_MODEL")
    if use_creds:
        for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            v = config.env(k)
            if v:
                env[k] = v
    return env


def deploy() -> None:
    sts = boto3.client("sts", region_name=REGION)
    print("identity:", sts.get_caller_identity()["Arn"])
    ecr = boto3.client("ecr", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    apprunner = boto3.client("apprunner", region_name=REGION)

    uri = ensure_ecr(ecr)
    image = docker_push(ecr, uri)
    print("image:", image)

    access_arn = ensure_role(
        iam, ECR_ACCESS_ROLE, "build.apprunner.amazonaws.com",
        policy_arn="arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess")
    instance_arn = ensure_role(
        iam, INSTANCE_ROLE, "tasks.apprunner.amazonaws.com",
        inline={"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                       "bedrock:ListFoundationModels", "bedrock:Converse",
                       "bedrock:ConverseStream"],
            "Resource": "*"}]})
    if not access_arn:
        print("FATAL: App Runner needs an ECR access role. Ask an admin or use the EC2 path "
              "in deploy/README.md")
        return

    use_env_creds = instance_arn is None
    src = {
        "AuthenticationConfiguration": {"AccessRoleArn": access_arn},
        "AutoDeploymentsEnabled": False,
        "ImageRepository": {
            "ImageIdentifier": image,
            "ImageRepositoryType": "ECR",
            "ImageConfiguration": {"Port": "8000",
                                   "RuntimeEnvironmentVariables": env_vars(use_env_creds)},
        },
    }
    inst_cfg = {"Cpu": "1024", "Memory": "2048"}
    if instance_arn:
        inst_cfg["InstanceRoleArn"] = instance_arn

    existing = [s for s in apprunner.list_services()["ServiceSummaryList"]
                if s["ServiceName"] == SERVICE]
    if existing:
        arn = existing[0]["ServiceArn"]
        print("updating existing service...")
        apprunner.update_service(ServiceArn=arn, SourceConfiguration=src,
                                 InstanceConfiguration=inst_cfg)
    else:
        print("creating App Runner service...")
        out = apprunner.create_service(
            ServiceName=SERVICE, SourceConfiguration=src, InstanceConfiguration=inst_cfg,
            HealthCheckConfiguration={"Protocol": "HTTP", "Path": "/health",
                                      "Interval": 10, "Timeout": 5,
                                      "HealthyThreshold": 1, "UnhealthyThreshold": 5})
        arn = out["Service"]["ServiceArn"]
    print("waiting for service (2-5 min)...")
    for _ in range(60):
        time.sleep(10)
        svc = apprunner.describe_service(ServiceArn=arn)["Service"]
        print("  status:", svc["Status"])
        if svc["Status"] == "RUNNING":
            print(f"\nDEPLOYED: https://{svc['ServiceUrl']}")
            print(f"health:   https://{svc['ServiceUrl']}/health")
            if use_env_creds:
                print("NOTE: service uses the session creds from backend/.env — they expire; "
                      "refresh with: python deploy/deploy_aws.py --update-env")
            return
        if svc["Status"] in ("CREATE_FAILED", "UPDATE_FAILED"):
            print("FAILED — check App Runner logs in console; EC2 fallback in deploy/README.md")
            return
    print("timed out waiting; check console")


def status() -> None:
    apprunner = boto3.client("apprunner", region_name=REGION)
    for s in apprunner.list_services()["ServiceSummaryList"]:
        if s["ServiceName"] == SERVICE:
            svc = apprunner.describe_service(ServiceArn=s["ServiceArn"])["Service"]
            print(svc["Status"], f"https://{svc['ServiceUrl']}")
            return
    print("service not found")


def update_env() -> None:
    apprunner = boto3.client("apprunner", region_name=REGION)
    ss = [s for s in apprunner.list_services()["ServiceSummaryList"]
          if s["ServiceName"] == SERVICE]
    if not ss:
        print("service not found")
        return
    arn = ss[0]["ServiceArn"]
    svc = apprunner.describe_service(ServiceArn=arn)["Service"]
    src = svc["SourceConfiguration"]
    src["ImageRepository"]["ImageConfiguration"]["RuntimeEnvironmentVariables"] = env_vars(True)
    apprunner.update_service(ServiceArn=arn, SourceConfiguration=src)
    print("env creds refreshed; service redeploying")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    elif "--update-env" in sys.argv:
        update_env()
    else:
        deploy()
