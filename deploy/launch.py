"""Create the Study Corner demo server on EC2 (PAID: about $20 a month while it runs).

    python deploy/package.py    # first: code + lectures to S3, secrets to SSM
    python deploy/launch.py     # role, security group and a t3.small server

Creates (or reuses):
    IAM role + instance profile  study-corner-server   only what the app needs
    security group               study-corner-web       inbound 80 only; no SSH
    EC2 instance                 Name=study-corner      Amazon Linux 2023, t3.small, 20 GB

The server downloads the code and lectures from S3, reads the YouTube key and
the demo passcode from SSM Parameter Store when the app starts, and runs it
as a systemd service on port 80. Admin access is through SSM (no SSH keys).
Remove everything with: python deploy/teardown.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lecture_agent import config, db  # noqa: E402
from lecture_agent.transcribe import default_bucket_name  # noqa: E402

NAME = "study-corner"
ROLE = "study-corner-server"
SG = "study-corner-web"
INSTANCE_TYPE = "t3.small"

s = config.session()
REGION = config.REGION
ACCOUNT = s.client("sts").get_caller_identity()["Account"]
BUCKET = default_bucket_name(ACCOUNT)
ec2, iam, ssm = s.client("ec2"), s.client("iam"), s.client("ssm")

POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {"Sid": "Bedrock", "Effect": "Allow",
         "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], "Resource": "*"},
        {"Sid": "Speech", "Effect": "Allow",
         "Action": ["transcribe:StartTranscriptionJob", "transcribe:GetTranscriptionJob",
                    "transcribe:DeleteTranscriptionJob", "translate:TranslateText",
                    "comprehend:DetectDominantLanguage"], "Resource": "*"},
        {"Sid": "Table", "Effect": "Allow",
         "Action": ["dynamodb:DescribeTable", "dynamodb:Query", "dynamodb:Scan", "dynamodb:GetItem",
                    "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"],
         "Resource": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{db.TABLE}"},
        {"Sid": "BucketList", "Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": f"arn:aws:s3:::{BUCKET}"},
        {"Sid": "Deploy", "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": f"arn:aws:s3:::{BUCKET}/deploy/*"},
        {"Sid": "Audio", "Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
         "Resource": f"arn:aws:s3:::{BUCKET}/audio/*"},
        {"Sid": "Secrets", "Effect": "Allow", "Action": ["ssm:GetParameter"],
         "Resource": f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/study-corner/*"},
        {"Sid": "SecretsKey", "Effect": "Allow", "Action": ["kms:Decrypt"], "Resource": "*",
         "Condition": {"StringEquals": {"kms:ViaService": f"ssm.{REGION}.amazonaws.com"}}},
    ],
}

USER_DATA = f"""#!/bin/bash
set -euxo pipefail
exec > /var/log/study-corner-setup.log 2>&1
dnf install -y python3.11 python3.11-pip dejavu-sans-fonts tar gzip
# 1 GB of swap, in case a long lecture needs more memory than the 2 GB t3.small
if [ ! -f /swapfile ]; then fallocate -l 1G /swapfile; chmod 600 /swapfile; mkswap /swapfile; swapon /swapfile;
  echo '/swapfile swap swap defaults 0 0' >> /etc/fstab; fi
id studycorner || useradd --system --create-home --home-dir /opt/study-corner studycorner
cd /opt/study-corner
mkdir -p app
aws s3 cp --region {REGION} s3://{BUCKET}/deploy/code.tar.gz /tmp/code.tar.gz
tar -xzf /tmp/code.tar.gz -C app
if [ ! -d app/output/web ]; then
  aws s3 cp --region {REGION} s3://{BUCKET}/deploy/lectures.tar.gz /tmp/lectures.tar.gz
  tar -xzf /tmp/lectures.tar.gz -C app
fi
[ -d venv ] || python3.11 -m venv venv
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r app/requirements.txt

cat > /opt/study-corner/start.sh <<'EOF'
#!/bin/bash
set -euo pipefail
get() {{ aws ssm get-parameter --region {REGION} --with-decryption --name "$1" --query Parameter.Value --output text; }}
umask 077
printf 'YOUTUBE_API_KEY=%s\\n' "$(get /study-corner/youtube-api-key)" > /opt/study-corner/app/.env
if [ "${{STUDY_CORNER_DEMO:-0}}" = "1" ]; then export LECTURE_DEMO_PASSCODE="$(get /study-corner/demo-passcode)"; fi
# webshare proxy (optional, set with deploy/proxy.py): YouTube blocks caption requests from AWS
opt() {{ aws ssm get-parameter --region {REGION} --with-decryption --name "$1" --query Parameter.Value --output text 2>/dev/null || true; }}
proxy_user="$(opt /study-corner/webshare-username)"; proxy_pass="$(opt /study-corner/webshare-password)"
if [ -n "$proxy_user" ] && [ -n "$proxy_pass" ]; then
  printf 'WEBSHARE_PROXY_USERNAME=%s\\nWEBSHARE_PROXY_PASSWORD=%s\\n' "$proxy_user" "$proxy_pass" >> /opt/study-corner/app/.env
fi
export LECTURE_AWS_REGION={REGION}
exec /opt/study-corner/venv/bin/python -X utf8 -m lecture_agent.web --host 0.0.0.0 --port 80
EOF
chmod 755 /opt/study-corner/start.sh

cat > /etc/systemd/system/study-corner.service <<'EOF'
[Unit]
Description=Study Corner web app
After=network-online.target
Wants=network-online.target
[Service]
User=studycorner
WorkingDirectory=/opt/study-corner/app
ExecStart=/opt/study-corner/start.sh
Restart=always
RestartSec=3
AmbientCapabilities=CAP_NET_BIND_SERVICE
Environment=PYTHONIOENCODING=utf-8
[Install]
WantedBy=multi-user.target
EOF
chown -R studycorner:studycorner /opt/study-corner
systemctl daemon-reload
systemctl enable --now study-corner
echo SETUP-DONE
"""


def role() -> None:
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
                                                     "Action": "sts:AssumeRole"}]}
    try:
        iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                        Description="Study Corner demo server", Tags=[{"Key": "app", "Value": NAME}])
        print("created role", ROLE)
    except iam.exceptions.EntityAlreadyExistsException:
        print("reusing role", ROLE)
    iam.put_role_policy(RoleName=ROLE, PolicyName="study-corner-app", PolicyDocument=json.dumps(POLICY))
    iam.attach_role_policy(RoleName=ROLE, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    try:
        iam.create_instance_profile(InstanceProfileName=ROLE)
        iam.add_role_to_instance_profile(InstanceProfileName=ROLE, RoleName=ROLE)
        print("created instance profile; waiting for IAM to propagate")
        time.sleep(15)
    except iam.exceptions.EntityAlreadyExistsException:
        pass


def security_group() -> str:
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    found = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [SG]},
                                                  {"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"]
    if found:
        print("reusing security group", found[0]["GroupId"])
        return found[0]["GroupId"]
    sg = ec2.create_security_group(GroupName=SG, Description="Study Corner: HTTP only", VpcId=vpc,
                                   TagSpecifications=[{"ResourceType": "security-group",
                                                       "Tags": [{"Key": "app", "Value": NAME}]}])["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
         "IpRanges": [{"CidrIp": "0.0.0.0/0"}], "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}])
    print("created security group", sg, "(port 80 only)")
    return sg


def existing() -> dict | None:
    res = ec2.describe_instances(Filters=[{"Name": "tag:Name", "Values": [NAME]},
                                          {"Name": "instance-state-name", "Values": ["pending", "running"]}])
    for r in res["Reservations"]:
        for i in r["Instances"]:
            return i
    return None


def main() -> None:
    inst = existing()
    if inst:
        print("already running:", inst["InstanceId"], "http://" + inst.get("PublicIpAddress", "?"))
        return
    role()
    sg = security_group()
    ami = ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")["Parameter"]["Value"]
    for attempt in range(5):
        try:
            inst = ec2.run_instances(
                ImageId=ami, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
                IamInstanceProfile={"Name": ROLE}, SecurityGroupIds=[sg], UserData=USER_DATA,
                MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
                BlockDeviceMappings=[{"DeviceName": "/dev/xvda",
                                      "Ebs": {"VolumeSize": 20, "VolumeType": "gp3", "DeleteOnTermination": True}}],
                TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": NAME},
                                                                         {"Key": "app", "Value": NAME}]}],
            )["Instances"][0]
            break
        except ec2.exceptions.ClientError as exc:
            if "instance profile" in str(exc).lower() and attempt < 4:
                time.sleep(10)  # IAM not propagated yet
                continue
            raise
    print("started", inst["InstanceId"], "- waiting for it to run")
    ec2.get_waiter("instance_running").wait(InstanceIds=[inst["InstanceId"]])
    inst = ec2.describe_instances(InstanceIds=[inst["InstanceId"]])["Reservations"][0]["Instances"][0]
    print("INSTANCE", inst["InstanceId"])
    print("URL http://" + inst["PublicIpAddress"])
    print("setup takes about 3-5 minutes (python packages); log: /var/log/study-corner-setup.log")


if __name__ == "__main__":
    main()
