"""Package Study Corner for the EC2 server and upload it to S3 (PAID: a few cents of S3).

    python deploy/package.py

Uploads to s3://<bucket>/deploy/:
    code.tar.gz     the committed code (git archive HEAD)
    lectures.tar.gz the finished lectures in output/web (no chats, no audio, no PDFs)

and stores two SecureString parameters in SSM Parameter Store:
    /study-corner/youtube-api-key   from .env
    /study-corner/demo-passcode     made here unless it already exists

Nothing secret is written into the code, the archive or the server's user data.
"""

import io
import json
import secrets
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lecture_agent import config  # noqa: E402
from lecture_agent.transcribe import default_bucket_name  # noqa: E402

SKIP_NAMES = {"audio.flac", "notes.pdf", "job.tmp"}
SKIP_DIRS = {"chat"}


def code_archive() -> bytes:
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        print("note: uncommitted changes are NOT included:\n" + dirty)
    return subprocess.run(["git", "archive", "--format=tar.gz", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout


def lectures_archive() -> tuple[bytes, int]:
    buf, count = io.BytesIO(), 0
    web = ROOT / "output" / "web"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for job in sorted(web.iterdir()):
            meta = job / "job.json"
            if not meta.exists() or json.loads(meta.read_text(encoding="utf-8")).get("status") != "done":
                continue
            count += 1
            for path in job.rglob("*"):
                rel = path.relative_to(web)
                if path.is_file() and path.name not in SKIP_NAMES and not SKIP_DIRS & set(rel.parts):
                    tar.add(path, arcname=f"output/web/{rel.as_posix()}")
    return buf.getvalue(), count


def main() -> None:
    s = config.session()
    account = s.client("sts").get_caller_identity()["Account"]
    bucket = default_bucket_name(account)
    s3, ssm = s.client("s3"), s.client("ssm")

    code = code_archive()
    lectures, n = lectures_archive()
    s3.put_object(Bucket=bucket, Key="deploy/code.tar.gz", Body=code)
    s3.put_object(Bucket=bucket, Key="deploy/lectures.tar.gz", Body=lectures)
    print(f"uploaded code {len(code) / 1e6:.2f} MB and {n} lectures {len(lectures) / 1e6:.1f} MB to s3://{bucket}/deploy/")

    key = config.YOUTUBE_API_KEY
    if key:
        ssm.put_parameter(Name="/study-corner/youtube-api-key", Value=key, Type="SecureString", Overwrite=True)
        print("stored /study-corner/youtube-api-key (SecureString)")
    try:
        ssm.get_parameter(Name="/study-corner/demo-passcode")
        print("kept the existing /study-corner/demo-passcode")
    except ssm.exceptions.ParameterNotFound:
        ssm.put_parameter(Name="/study-corner/demo-passcode", Value="judge-" + secrets.token_hex(4),
                          Type="SecureString")
        print("created /study-corner/demo-passcode (SecureString)")
    print("BUCKET", bucket)


if __name__ == "__main__":
    main()
