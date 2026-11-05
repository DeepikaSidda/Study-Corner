"""Ship the latest committed code to the running server and restart it (lectures on the server are kept).

    git commit ...                  # the server gets what is committed
    python deploy/update.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy.remote import run  # noqa: E402
from lecture_agent import config  # noqa: E402
from lecture_agent.transcribe import default_bucket_name  # noqa: E402

s = config.session()
bucket = default_bucket_name(s.client("sts").get_caller_identity()["Account"])
code = subprocess.run(["git", "archive", "--format=tar.gz", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
s.client("s3").put_object(Bucket=bucket, Key="deploy/code.tar.gz", Body=code)
head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
print(f"uploaded code at {head}")

status, out = run(f"""set -e
cd /opt/study-corner
aws s3 cp --quiet --region {config.REGION} s3://{bucket}/deploy/code.tar.gz /tmp/code.tar.gz
tar -xzf /tmp/code.tar.gz -C app
venv/bin/pip install --quiet -r app/requirements.txt
chown -R studycorner:studycorner /opt/study-corner
systemctl restart study-corner
sleep 4
systemctl is-active study-corner
curl -s -o /dev/null -w 'home page HTTP %{{http_code}}\\n' http://127.0.0.1/""")
print(out)
print("STATUS", status)
