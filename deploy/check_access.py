"""Check that the AWS user can deploy Study Corner to EC2. Creates nothing.

Uses EC2 DryRun calls and IAM policy simulation (falls back to read-only calls
if simulation isn't allowed).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lecture_agent import config  # noqa: E402

s = config.session()
who = s.client("sts").get_caller_identity()
print("identity:", who["Arn"])
ec2, iam = s.client("ec2"), s.client("iam")

# Default VPC and a current Amazon Linux 2023 image
vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
print("default VPC:", vpcs[0]["VpcId"] if vpcs else "NONE")
ssm = s.client("ssm")
ami = ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")["Parameter"]["Value"]
print("AL2023 AMI:", ami)

try:
    ec2.run_instances(ImageId=ami, InstanceType="t3.small", MinCount=1, MaxCount=1, DryRun=True)
except ec2.exceptions.ClientError as exc:
    code = exc.response["Error"]["Code"]
    print("RunInstances dry run:", "ALLOWED" if code == "DryRunOperation" else f"DENIED ({code})")

actions = ["iam:CreateRole", "iam:PutRolePolicy", "iam:AttachRolePolicy", "iam:CreateInstanceProfile",
           "iam:AddRoleToInstanceProfile", "iam:PassRole", "ec2:CreateSecurityGroup",
           "ec2:AuthorizeSecurityGroupIngress", "ec2:RunInstances", "ec2:CreateTags", "ssm:SendCommand",
           "ssm:PutParameter", "s3:PutObject", "ec2:TerminateInstances"]
try:
    res = iam.simulate_principal_policy(PolicySourceArn=who["Arn"], ActionNames=actions)
    for r in res["EvaluationResults"]:
        print(f"  {r['EvalActionName']:36} {r['EvalDecision']}")
except Exception as exc:  # noqa: BLE001
    print("policy simulation not allowed:", type(exc).__name__)
    try:
        names = [p["PolicyName"] for p in iam.list_attached_user_policies(UserName=who["Arn"].split("/")[-1])["AttachedPolicies"]]
        print("attached user policies:", names)
    except Exception as exc2:  # noqa: BLE001
        print("cannot list policies:", type(exc2).__name__)
