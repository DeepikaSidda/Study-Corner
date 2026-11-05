"""AWS session and model configuration.

Everything that talks to AWS goes through `session()`, so the account in use is
decided in one place and can be printed before any money is spent.

Settings (environment variables):
    LECTURE_AWS_PROFILE   named profile to use. When set, ambient
                          AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are removed
                          from this process first, because boto3 ranks
                          environment credentials ABOVE profiles and would
                          otherwise silently use a different account.
    LECTURE_AWS_REGION    default us-east-1
    LECTURE_MODEL_ID      default us.amazon.nova-pro-v1:0 (vision + structured
                          output, no Marketplace subscription needed)
    LECTURE_MAX_TOKENS    default 8000
    LECTURE_S3_BUCKET     bucket for Transcribe uploads (optional)
    YOUTUBE_API_KEY       YouTube Data API v3 key; enables "Paste a YouTube link"
    WEBSHARE_PROXY_USERNAME, WEBSHARE_PROXY_PASSWORD
                          optional Webshare residential proxy login. YouTube
                          blocks caption requests from cloud servers (AWS),
                          so the server fetches captions through it

Settings can also go in a `.env` file in the project folder (git-ignored).
Real environment variables win over `.env`.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _load_env_file(path: Path = ENV_FILE) -> None:
    """Read KEY=value lines from .env without overriding the real environment."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY") or None
WEBSHARE_PROXY_USERNAME = os.environ.get("WEBSHARE_PROXY_USERNAME") or None
WEBSHARE_PROXY_PASSWORD = os.environ.get("WEBSHARE_PROXY_PASSWORD") or None
REGION = os.environ.get("LECTURE_AWS_REGION", "us-east-1")
PROFILE = os.environ.get("LECTURE_AWS_PROFILE") or None
MODEL_ID = os.environ.get("LECTURE_MODEL_ID", "us.amazon.nova-pro-v1:0")
MAX_TOKENS = int(os.environ.get("LECTURE_MAX_TOKENS", "8000"))

_SESSION = None


def session():
    """The single boto3 session used for every AWS call."""
    global _SESSION
    if _SESSION is None:
        import boto3

        if PROFILE:
            for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
                os.environ.pop(var, None)
            _SESSION = boto3.Session(profile_name=PROFILE, region_name=REGION)
        else:
            _SESSION = boto3.Session(region_name=REGION)
    return _SESSION


def whoami() -> dict:
    """Return and describe the AWS identity that will be billed."""
    ident = session().client("sts").get_caller_identity()
    return {"account": ident["Account"], "arn": ident["Arn"], "region": REGION, "model": MODEL_ID}


def model(temperature: float = 0.2, max_tokens: int | None = None):
    """A Bedrock model bound to the configured session."""
    from strands.models import BedrockModel

    return BedrockModel(
        model_id=MODEL_ID,
        boto_session=session(),
        max_tokens=max_tokens or MAX_TOKENS,
        temperature=temperature,
    )
