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
"""

from __future__ import annotations

import os

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


def model(temperature: float = 0.2):
    """A Bedrock model bound to the configured session."""
    from strands.models import BedrockModel

    return BedrockModel(
        model_id=MODEL_ID,
        boto_session=session(),
        max_tokens=MAX_TOKENS,
        temperature=temperature,
    )
