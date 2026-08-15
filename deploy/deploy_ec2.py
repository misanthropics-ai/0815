"""No-docker AWS deploy: S3 code bundle + EC2 (Amazon Linux 2023) + uvicorn.

  python deploy/deploy_ec2.py            # zip -> S3 -> launch EC2 -> print URL
  python deploy/deploy_ec2.py --status   # find instance + health URL
  python deploy/deploy_ec2.py --update   # legacy SSM code update before CI/CD migration
  python deploy/deploy_ec2.py --terminate

Creates an instance role with Bedrock permissions so no runtime credentials are
copied to the box. Deployment stops if IAM is unavailable. Security group opens
port 8000; prefer the GitHub OIDC + SSM workflow for repeat deployments.
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import config  # noqa: E402

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

REGION = config.AWS_REGION
TAG = "ai-rec-diagnostics"
ROLE = "EC2Bedrock0815"
PROFILE = "EC2Bedrock0815Profile"

EXCLUDE = {".env", "data", "__pycache__", ".venv"}


def make_zip() -> bytes:
    root = Path(__file__).resolve().parent.parent
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for sub in ("backend", "contracts"):
            for p in (root / sub).rglob("*"):
                if p.is_dir() or any(part in EXCLUDE for part in p.parts):
                    continue
                z.write(p, p.relative_to(root))
    return buf.getvalue()


def ensure_instance_profile(iam) -> str | None:
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole"}]}
    policy = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow",
        "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                   "bedrock:ListFoundationModels"],
        "Resource": "*"}]}
    try:
        try:
            iam.get_role(RoleName=ROLE)
        except iam.exceptions.NoSuchEntityException:
            iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust))
        iam.put_role_policy(RoleName=ROLE, PolicyName="bedrock", PolicyDocument=json.dumps(policy))
        try:  # SSM access for in-place debugging (aws ssm start-session)
            iam.attach_role_policy(RoleName=ROLE,
                                   PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
        except ClientError:
            pass
        try:
            iam.get_instance_profile(InstanceProfileName=PROFILE)
        except iam.exceptions.NoSuchEntityException:
            iam.create_instance_profile(InstanceProfileName=PROFILE)
            time.sleep(2)
        roles = iam.get_instance_profile(InstanceProfileName=PROFILE)["InstanceProfile"]["Roles"]
        if not any(r["RoleName"] == ROLE for r in roles):
            iam.add_role_to_instance_profile(InstanceProfileName=PROFILE, RoleName=ROLE)
        time.sleep(10)  # propagation
        return PROFILE
    except ClientError as e:
        print(f"  ! IAM unavailable ({e.response['Error']['Code']}); refusing to embed credentials")
        return None


def ensure_sg(ec2) -> str:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
    vpc_id = vpcs[0]["VpcId"]
    try:
        sg = ec2.create_security_group(GroupName=f"{TAG}-sg", Description="backend 8000",
                                       VpcId=vpc_id)
        sg_id = sg["GroupId"]
        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 8000, "ToPort": 8000,
             "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidGroup.Duplicate":
            raise
        sg_id = ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [f"{TAG}-sg"]}])["SecurityGroups"][0]["GroupId"]
    return sg_id


def user_data(presigned: str) -> str:
    env_lines = [f"AWS_DEFAULT_REGION={REGION}", "MODE=auto", "PORT=8000"]
    env_blob = "\n".join(env_lines)
    return f"""#!/bin/bash
set -x
dnf install -y python3.11 python3.11-pip unzip
mkdir -p /opt/app && cd /opt/app
curl -sf -o code.zip '{presigned}'
unzip -o code.zip
python3.11 -m pip install -r backend/requirements.txt
cat > backend/.env <<'EOF'
{env_blob}
EOF
cat > /etc/systemd/system/backend.service <<'EOF'
[Unit]
Description=ai-rec-diagnostics backend
After=network.target
[Service]
WorkingDirectory=/opt/app
ExecStart=/usr/bin/python3.11 -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now backend
"""


def find_eip(ec2) -> dict | None:
    """Return the tagged Elastic IP without allocating or changing resources."""
    addrs = ec2.describe_addresses(Filters=[{"Name": "tag:app", "Values": [TAG]}])["Addresses"]
    return addrs[0] if addrs else None


def ensure_eip(ec2) -> dict | None:
    """Reuse or allocate an Elastic IP for a stable demo URL."""
    try:
        existing = find_eip(ec2)
        if existing:
            return existing
        alloc = ec2.allocate_address(
            Domain="vpc",
            TagSpecifications=[{"ResourceType": "elastic-ip",
                                "Tags": [{"Key": "app", "Value": TAG}]}])
        return {"AllocationId": alloc["AllocationId"], "PublicIp": alloc["PublicIp"]}
    except ClientError as e:
        print(f"  ! Elastic IP unavailable ({e.response['Error']['Code']}); using dynamic IP")
        return None


def find_instance(ec2) -> dict | None:
    out = ec2.describe_instances(Filters=[
        {"Name": "tag:app", "Values": [TAG]},
        {"Name": "instance-state-name", "Values": ["pending", "running"]}])
    for res in out["Reservations"]:
        for inst in res["Instances"]:
            return inst
    return None


def deploy() -> None:
    session = boto3.Session(region_name=REGION)
    sts = session.client("sts")
    account = sts.get_caller_identity()["Account"]
    print("account:", account)
    s3 = session.client("s3")
    ec2 = session.client("ec2")
    iam = session.client("iam")
    ssm = session.client("ssm")

    bucket = f"{TAG}-{account}"
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)
    blob = make_zip()
    s3.put_object(Bucket=bucket, Key="code.zip", Body=blob)
    presigned = s3.generate_presigned_url("get_object",
                                          Params={"Bucket": bucket, "Key": "code.zip"},
                                          ExpiresIn=3600)
    print(f"code.zip uploaded ({len(blob) // 1024} KB)")

    profile = ensure_instance_profile(iam)
    if not profile:
        raise RuntimeError("EC2 instance role is required; runtime credentials will not be embedded")
    sg_id = ensure_sg(ec2)
    ami = ssm.get_parameter(
        Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")["Parameter"]["Value"]

    existing = find_instance(ec2)
    if existing:
        print(f"terminating old instance {existing['InstanceId']}...")
        ec2.terminate_instances(InstanceIds=[existing["InstanceId"]])
        ec2.get_waiter("instance_terminated").wait(InstanceIds=[existing["InstanceId"]])

    kwargs = dict(
        ImageId=ami, InstanceType="t3.small", MinCount=1, MaxCount=1,
        SecurityGroupIds=[sg_id],
        UserData=user_data(presigned),
        TagSpecifications=[{"ResourceType": "instance",
                            "Tags": [{"Key": "app", "Value": TAG},
                                     {"Key": "Name", "Value": TAG}]}])
    if profile:
        kwargs["IamInstanceProfile"] = {"Name": profile}
    try:
        inst = ec2.run_instances(**kwargs)["Instances"][0]
    except ClientError as e:
        if profile and "Invalid IAM Instance Profile" in str(e):
            time.sleep(15)
            inst = ec2.run_instances(**kwargs)["Instances"][0]
        else:
            raise
    iid = inst["InstanceId"]
    print("instance:", iid, "(waiting for running state...)")
    ec2.get_waiter("instance_running").wait(InstanceIds=[iid])
    eip = ensure_eip(ec2)
    if eip:
        ec2.associate_address(AllocationId=eip["AllocationId"], InstanceId=iid,
                              AllowReassociation=True)
        ip = eip["PublicIp"]
        print("elastic IP associated — URL stays the same across redeploys")
    else:
        desc = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
        ip = desc.get("PublicIpAddress")
    print(f"\nLAUNCHED: http://{ip}:8000  (boot + pip install takes ~2-4 min)")
    print(f"health:   http://{ip}:8000/health")
    print("status:   python deploy/deploy_ec2.py --status")


def status() -> None:
    ec2 = boto3.client("ec2", region_name=REGION)
    inst = find_instance(ec2)
    if not inst:
        print("no instance")
        return
    ip = inst.get("PublicIpAddress")
    print(inst["InstanceId"], inst["State"]["Name"], f"http://{ip}:8000/health" if ip else "")
    eip = find_eip(ec2)
    print("(elastic IP — stable across redeploys)" if eip and
          eip.get("PublicIp") == ip else "(dynamic IP)")


def update_in_place() -> None:
    """Push new code to the RUNNING instance via SSM (keeps DB/caches, same IP, ~30s)."""
    session = boto3.Session(region_name=REGION)
    ec2 = session.client("ec2")
    s3 = session.client("s3")
    ssm = session.client("ssm")
    sts = session.client("sts")
    inst = find_instance(ec2)
    if not inst:
        print("no running instance — do a full deploy first")
        return
    iid = inst["InstanceId"]
    info = ssm.describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": [iid]}])["InstanceInformationList"]
    if not info:
        raise RuntimeError(
            f"{iid} is not SSM-managed; refusing a full deploy because it would replace the instance"
        )
    account = sts.get_caller_identity()["Account"]
    bucket = f"{TAG}-{account}"
    blob = make_zip()
    s3.put_object(Bucket=bucket, Key="code.zip", Body=blob)
    presigned = s3.generate_presigned_url("get_object",
                                          Params={"Bucket": bucket, "Key": "code.zip"},
                                          ExpiresIn=900)
    print(f"code.zip uploaded ({len(blob) // 1024} KB); updating {iid} in place...")
    cmd = ssm.send_command(
        InstanceIds=[iid], DocumentName="AWS-RunShellScript",
        Parameters={"commands": [
            "set -e", "cd /opt/app",
            f"curl -sf -o code.zip '{presigned}'",
            "unzip -oq code.zip",
            "python3.11 -m pip install -q -r backend/requirements.txt",
            "systemctl restart backend",
        ]})["Command"]["CommandId"]
    for _ in range(30):
        time.sleep(4)
        inv = ssm.get_command_invocation(CommandId=cmd, InstanceId=iid)
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            print("update:", inv["Status"])
            if inv["Status"] != "Success":
                print((inv.get("StandardErrorContent") or "")[-800:])
            else:
                ip = inst.get("PublicIpAddress")
                print(f"live (data preserved): http://{ip}:8000/health")
            return
    print("timed out waiting for SSM command")


def terminate() -> None:
    ec2 = boto3.client("ec2", region_name=REGION)
    inst = find_instance(ec2)
    if inst:
        ec2.terminate_instances(InstanceIds=[inst["InstanceId"]])
        print("terminating", inst["InstanceId"])
    else:
        print("no instance")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    elif "--update" in sys.argv:
        update_in_place()
    elif "--terminate" in sys.argv:
        terminate()
    else:
        deploy()
