"""Run a shell command on the Study Corner server through AWS Systems Manager (no SSH).

    python deploy/remote.py "systemctl status study-corner --no-pager"
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lecture_agent import config  # noqa: E402


def instance_id() -> str:
    ec2 = config.session().client("ec2")
    res = ec2.describe_instances(Filters=[{"Name": "tag:Name", "Values": ["study-corner"]},
                                          {"Name": "instance-state-name", "Values": ["running"]}])
    return res["Reservations"][0]["Instances"][0]["InstanceId"]


def run(command: str, timeout: int = 600) -> tuple[str, str]:
    ssm = config.session().client("ssm")
    iid = instance_id()
    cmd = ssm.send_command(InstanceIds=[iid], DocumentName="AWS-RunShellScript",
                           Parameters={"commands": [command]}, TimeoutSeconds=timeout)["Command"]["CommandId"]
    while True:
        time.sleep(2)
        try:
            out = ssm.get_command_invocation(CommandId=cmd, InstanceId=iid)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if out["Status"] not in ("Pending", "InProgress", "Delayed"):
            return out["Status"], out["StandardOutputContent"] + out["StandardErrorContent"]


if __name__ == "__main__":
    status, text = run(" ".join(sys.argv[1:]))
    print(text)
    print("STATUS", status)
