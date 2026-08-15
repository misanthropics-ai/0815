#!/usr/bin/env python3
"""One-time AWS/GitHub bootstrap for the demo deployment workflow.

Creates or configures ECR, ensures the account-level GitHub OIDC provider,
deploys the least-privilege CloudFormation stack, and optionally writes only
non-secret repository variables with the GitHub CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy" / "cloudformation" / "github-actions-demo.yaml"
OIDC_URL = "https://token.actions.githubusercontent.com"
OIDC_AUDIENCE = "sts.amazonaws.com"
APP_TAG = "ai-rec-diagnostics"


def github_subject(owner: str, repository: str, branch: str) -> str:
    return f"repo:{owner}/{repository}:ref:refs/heads/{branch}"


def ensure_oidc_provider(iam) -> str:
    for item in iam.list_open_id_connect_providers().get("OpenIDConnectProviderList", []):
        arn = item["Arn"]
        provider = iam.get_open_id_connect_provider(OpenIDConnectProviderArn=arn)
        if provider.get("Url", "").rstrip("/") == OIDC_URL.removeprefix("https://"):
            clients = set(provider.get("ClientIDList", []))
            if OIDC_AUDIENCE not in clients:
                iam.add_client_id_to_open_id_connect_provider(
                    OpenIDConnectProviderArn=arn,
                    ClientID=OIDC_AUDIENCE,
                )
            return arn
    return iam.create_open_id_connect_provider(
        Url=OIDC_URL,
        ClientIDList=[OIDC_AUDIENCE],
    )["OpenIDConnectProviderArn"]


def ensure_ecr(ecr, repository: str) -> None:
    try:
        ecr.describe_repositories(repositoryNames=[repository])
    except ecr.exceptions.RepositoryNotFoundException:
        ecr.create_repository(
            repositoryName=repository,
            imageTagMutability="IMMUTABLE",
            imageScanningConfiguration={"scanOnPush": True},
            encryptionConfiguration={"encryptionType": "AES256"},
        )
    else:
        ecr.put_image_tag_mutability(
            repositoryName=repository,
            imageTagMutability="IMMUTABLE",
        )
        ecr.put_image_scanning_configuration(
            repositoryName=repository,
            imageScanningConfiguration={"scanOnPush": True},
        )
    ecr.put_lifecycle_policy(
        repositoryName=repository,
        lifecyclePolicyText=json.dumps(
            {
                "rules": [
                    {
                        "rulePriority": 1,
                        "description": "Keep the latest 30 immutable application images",
                        "selection": {
                            "tagStatus": "any",
                            "countType": "imageCountMoreThan",
                            "countNumber": 30,
                        },
                        "action": {"type": "expire"},
                    }
                ]
            }
        ),
    )


def discover_network(ec2, vpc_id: str | None, subnet_id: str | None) -> tuple[str, str]:
    if not vpc_id:
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            raise RuntimeError("no default VPC; pass --vpc-id and --subnet-id")
        vpc_id = vpcs[0]["VpcId"]
    if not subnet_id:
        subnets = ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "state", "Values": ["available"]},
            ]
        )["Subnets"]
        public = [subnet for subnet in subnets if subnet.get("MapPublicIpOnLaunch")]
        candidates = public or subnets
        if not candidates:
            raise RuntimeError(f"no available subnet in {vpc_id}")
        subnet_id = sorted(candidates, key=lambda item: item["AvailabilityZone"])[0]["SubnetId"]
    return vpc_id, subnet_id


def discover_existing_instance(ec2, requested_id: str | None) -> dict | None:
    if requested_id:
        reservations = ec2.describe_instances(InstanceIds=[requested_id])["Reservations"]
    else:
        reservations = ec2.describe_instances(
            Filters=[
                {"Name": "tag:app", "Values": [APP_TAG]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
        )["Reservations"]
    instances = [instance for item in reservations for instance in item["Instances"]]
    running = [instance for instance in instances if instance["State"]["Name"] == "running"]
    if requested_id and not running:
        raise RuntimeError(f"existing instance {requested_id} is not running")
    if len(running) > 1:
        ids = ", ".join(instance["InstanceId"] for instance in running)
        raise RuntimeError(f"multiple tagged instances found ({ids}); pass --existing-instance-id")
    return running[0] if running else None


def ensure_existing_instance_access(
    iam,
    instance: dict,
    *,
    account_id: str,
    region: str,
    repository: str,
) -> str:
    profile = instance.get("IamInstanceProfile")
    if not profile:
        raise RuntimeError("existing instance has no IAM instance profile")
    partition = profile["Arn"].split(":", 2)[1]
    profile_name = profile["Arn"].rsplit("/", 1)[-1]
    roles = iam.get_instance_profile(InstanceProfileName=profile_name)["InstanceProfile"]["Roles"]
    if len(roles) != 1:
        raise RuntimeError(f"expected one role in instance profile {profile_name}")
    role_name = roles[0]["RoleName"]
    iam.attach_role_policy(
        RoleName=role_name,
        PolicyArn=f"arn:{partition}:iam::aws:policy/AmazonSSMManagedInstanceCore",
    )
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="AiRecCicdRuntimeAccess",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "EcrLogin",
                        "Effect": "Allow",
                        "Action": "ecr:GetAuthorizationToken",
                        "Resource": "*",
                    },
                    {
                        "Sid": "PullApplicationImage",
                        "Effect": "Allow",
                        "Action": [
                            "ecr:BatchCheckLayerAvailability",
                            "ecr:BatchGetImage",
                            "ecr:GetDownloadUrlForLayer",
                        ],
                        "Resource": (
                            f"arn:{partition}:ecr:{region}:{account_id}:repository/{repository}"
                        ),
                    },
                    {
                        "Sid": "DiscoverBedrockModels",
                        "Effect": "Allow",
                        "Action": "bedrock:ListFoundationModels",
                        "Resource": "*",
                    },
                    {
                        "Sid": "InvokeBedrockModels",
                        "Effect": "Allow",
                        "Action": [
                            "bedrock:InvokeModel",
                            "bedrock:InvokeModelWithResponseStream",
                        ],
                        "Resource": "*",
                    },
                ],
            }
        ),
    )
    return role_name


def prepare_existing_instance(ssm, instance_id: str) -> None:
    for _ in range(30):
        managed = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )["InstanceInformationList"]
        if managed:
            break
        time.sleep(4)
    else:
        raise RuntimeError(f"instance {instance_id} did not register with SSM")

    command_id = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Comment="Prepare existing runtime for GitHub Actions deployments",
        Parameters={
            "commands": [
                "set -euo pipefail",
                "dnf install -y docker jq",
                "systemctl enable --now docker",
                "docker volume create ai-rec-data >/dev/null",
            ]
        },
    )["Command"]["CommandId"]
    for _ in range(60):
        try:
            invocation = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(2)
            continue
        if invocation["Status"] in {"Success", "Failed", "Cancelled", "TimedOut"}:
            if invocation["Status"] != "Success":
                error = invocation.get("StandardErrorContent", "").strip()
                raise RuntimeError(f"failed to prepare {instance_id}: {error}")
            return
        time.sleep(2)
    raise RuntimeError(f"timed out preparing {instance_id}")


def stack_outputs(cloudformation, stack_name: str) -> dict[str, str]:
    stack = cloudformation.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}


def deploy_stack(cloudformation, stack_name: str, parameters: list[dict[str, str]]) -> None:
    kwargs = {
        "StackName": stack_name,
        "TemplateBody": TEMPLATE.read_text(encoding="utf-8"),
        "Parameters": parameters,
        "Capabilities": ["CAPABILITY_NAMED_IAM"],
        "Tags": [
            {"Key": "App", "Value": "ai-rec-diagnostics"},
            {"Key": "ManagedBy", "Value": "bootstrap_cicd.py"},
        ],
    }
    try:
        cloudformation.describe_stacks(StackName=stack_name)
    except ClientError as exc:
        if "does not exist" not in str(exc):
            raise
        cloudformation.create_stack(**kwargs, OnFailure="DELETE")
        cloudformation.get_waiter("stack_create_complete").wait(StackName=stack_name)
        return
    try:
        cloudformation.update_stack(**kwargs)
    except ClientError as exc:
        if "No updates are to be performed" in str(exc):
            return
        raise
    cloudformation.get_waiter("stack_update_complete").wait(StackName=stack_name)


def configure_github(repository: str, values: dict[str, str]) -> None:
    for name, value in values.items():
        subprocess.run(
            ["gh", "variable", "set", name, "--repo", repository, "--body", value],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    parser.add_argument("--stack-name", default="ai-rec-diagnostics-cicd")
    parser.add_argument("--github-owner", default="misanthropics-ai")
    parser.add_argument("--github-repository", default="0815")
    parser.add_argument("--github-branch", default="main")
    parser.add_argument("--ecr-repository", default="ai-rec-diagnostics")
    parser.add_argument("--instance-type", default="t3.small")
    parser.add_argument("--allowed-cidr", default="0.0.0.0/0")
    parser.add_argument("--vpc-id")
    parser.add_argument("--subnet-id")
    instance_group = parser.add_mutually_exclusive_group()
    instance_group.add_argument("--existing-instance-id")
    instance_group.add_argument("--new-instance", action="store_true")
    parser.add_argument("--existing-api-url")
    parser.add_argument("--configure-github", action="store_true")
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    identity = session.client("sts").get_caller_identity()
    account_id = identity["Account"]
    print(f"AWS identity: {identity['Arn']}")
    print(
        "OIDC subject: "
        + github_subject(args.github_owner, args.github_repository, args.github_branch)
    )
    if args.allowed_cidr == "0.0.0.0/0":
        print("WARNING: port 8000 will be public; this stack is for demo/staging only")

    iam = session.client("iam")
    ecr = session.client("ecr")
    ec2 = session.client("ec2")
    ssm = session.client("ssm")
    cloudformation = session.client("cloudformation")
    oidc_arn = ensure_oidc_provider(iam)
    ensure_ecr(ecr, args.ecr_repository)
    existing = None
    if not args.new_instance:
        existing = discover_existing_instance(ec2, args.existing_instance_id)
    if existing:
        vpc_id = existing["VpcId"]
        subnet_id = existing["SubnetId"]
        public_host = existing.get("PublicIpAddress") or existing.get("PublicDnsName")
        api_url = args.existing_api_url or (f"http://{public_host}:8000" if public_host else None)
        if not api_url:
            raise RuntimeError("existing instance has no public address; pass --existing-api-url")
        role_name = ensure_existing_instance_access(
            iam,
            existing,
            account_id=account_id,
            region=args.region,
            repository=args.ecr_repository,
        )
        ec2.modify_instance_metadata_options(
            InstanceId=existing["InstanceId"],
            HttpEndpoint="enabled",
            HttpTokens="required",
            HttpPutResponseHopLimit=2,
        )
        prepare_existing_instance(ssm, existing["InstanceId"])
        print(
            f"Reusing {existing['InstanceId']} ({api_url}) with instance role {role_name}; "
            "no new EC2 instance will be created"
        )
    else:
        vpc_id, subnet_id = discover_network(ec2, args.vpc_id, args.subnet_id)
        api_url = ""

    values = {
        "GitHubOwner": args.github_owner,
        "GitHubRepository": args.github_repository,
        "GitHubBranch": args.github_branch,
        "GitHubOidcProviderArn": oidc_arn,
        "EcrRepositoryName": args.ecr_repository,
        "ExistingInstanceId": existing["InstanceId"] if existing else "",
        "ExistingPublicApiUrl": api_url,
        "VpcId": vpc_id,
        "SubnetId": subnet_id,
        "AllowedCidr": args.allowed_cidr,
        "InstanceType": args.instance_type,
    }
    deploy_stack(
        cloudformation,
        args.stack_name,
        [{"ParameterKey": key, "ParameterValue": value} for key, value in values.items()],
    )
    outputs = stack_outputs(cloudformation, args.stack_name)
    repo = f"{args.github_owner}/{args.github_repository}"
    github_variables = {
        "AWS_ACCOUNT_ID": account_id,
        "AWS_REGION": args.region,
        "AWS_DEPLOY_ROLE_ARN": outputs["DeployRoleArn"],
        "ECR_REPOSITORY": outputs["EcrRepositoryName"],
        "EC2_INSTANCE_ID": outputs["InstanceId"],
        "AWS_API_URL": outputs["PublicApiUrl"],
    }
    if args.configure_github:
        configure_github(repo, github_variables)

    print("\nCloudFormation outputs:")
    print(json.dumps(outputs, indent=2))
    print("\nGitHub repository variables (non-secret):")
    print(json.dumps(github_variables, indent=2))
    if not args.configure_github:
        print("Re-run with --configure-github to write these variables with the GitHub CLI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
