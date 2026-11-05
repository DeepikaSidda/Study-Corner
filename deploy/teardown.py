"""Delete the Study Corner demo server and everything launch.py made, so the cost stops.

    python deploy/teardown.py           # shows what would be deleted
    python deploy/teardown.py --yes     # deletes it

Removes: the EC2 instance (and its disk), the security group, the IAM role and
instance profile, the two SSM parameters, and s3://<bucket>/deploy/.
Keeps: the DynamoDB table and your local lectures.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lecture_agent import config  # noqa: E402
from lecture_agent.transcribe import default_bucket_name  # noqa: E402

YES = "--yes" in sys.argv
s = config.session()
ec2, iam, ssm, s3 = s.client("ec2"), s.client("iam"), s.client("ssm"), s.client("s3")
bucket = default_bucket_name(s.client("sts").get_caller_identity()["Account"])

ids = [i["InstanceId"] for r in ec2.describe_instances(Filters=[
    {"Name": "tag:Name", "Values": ["study-corner"]},
    {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])["Reservations"]
       for i in r["Instances"]]
sgs = [g["GroupId"] for g in ec2.describe_security_groups(
    Filters=[{"Name": "group-name", "Values": ["study-corner-web"]}])["SecurityGroups"]]
print("instances:", ids, "| security groups:", sgs, "| role: study-corner-server",
      "| SSM: /study-corner/* | S3:", f"s3://{bucket}/deploy/")
if not YES:
    print("dry run: nothing deleted. Run again with --yes to delete.")
    sys.exit(0)

if ids:
    ec2.terminate_instances(InstanceIds=ids)
    ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)
    print("terminated", ids)
for sg in sgs:
    for attempt in range(10):
        try:
            ec2.delete_security_group(GroupId=sg)
            print("deleted", sg)
            break
        except ec2.exceptions.ClientError:
            time.sleep(6)  # the network interface takes a moment to go away
try:
    iam.remove_role_from_instance_profile(InstanceProfileName="study-corner-server", RoleName="study-corner-server")
    iam.delete_instance_profile(InstanceProfileName="study-corner-server")
except iam.exceptions.NoSuchEntityException:
    pass
try:
    iam.detach_role_policy(RoleName="study-corner-server",
                           PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    iam.delete_role_policy(RoleName="study-corner-server", PolicyName="study-corner-app")
    iam.delete_role(RoleName="study-corner-server")
    print("deleted role study-corner-server")
except iam.exceptions.NoSuchEntityException:
    pass
for name in ("/study-corner/youtube-api-key", "/study-corner/demo-passcode",
             "/study-corner/webshare-username", "/study-corner/webshare-password"):
    try:
        ssm.delete_parameter(Name=name)
    except ssm.exceptions.ParameterNotFound:
        pass
for key in ("deploy/code.tar.gz", "deploy/lectures.tar.gz"):
    s3.delete_object(Bucket=bucket, Key=key)
print("done: the demo server and its resources are deleted")
