"""Amazon Transcribe backend.

Uploads the audio to S3, runs a batch transcription job, downloads the result,
then deletes the uploaded audio and the job. Results are cached by the
pipeline so a rerun does not pay for transcription twice.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid
from pathlib import Path

from . import config

# Languages Transcribe chooses between when language="auto" (env: LECTURE_LANGUAGE_OPTIONS).
LANGUAGE_OPTIONS = os.environ.get("LECTURE_LANGUAGE_OPTIONS", "en-US,en-IN,en-GB,hi-IN,ta-IN,te-IN")

FORMATS = {".flac": "flac", ".mp3": "mp3", ".mp4": "mp4", ".wav": "wav", ".m4a": "m4a", ".ogg": "ogg", ".webm": "webm"}


def default_bucket_name(account: str) -> str:
    return f"lecture-notes-agent-{account}-{config.REGION}"


def ensure_bucket(s3, bucket: str) -> None:
    """Create the upload bucket if it does not exist yet.

    New buckets block public access by default. The bucket holds an audio file
    only for the length of one job.
    """
    try:
        s3.head_bucket(Bucket=bucket)
        return
    except s3.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket", "NotFound"):
            raise
    if config.REGION == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": config.REGION})


def transcribe(
    audio_path: Path,
    language: str = "en-US",
    bucket: str | None = None,
    poll_seconds: float = 5.0,
    timeout_seconds: float = 3600.0,
    log=print,
) -> dict:
    """Run a Transcribe job and return its raw JSON result.

    language: a Transcribe language code such as en-US, en-IN or hi-IN, or
    "auto" to let Transcribe identify the language.
    """
    fmt = FORMATS.get(audio_path.suffix.lower())
    if fmt is None:
        raise ValueError(f"Unsupported audio format: {audio_path.suffix}")

    sess = config.session()
    account = sess.client("sts").get_caller_identity()["Account"]
    s3 = sess.client("s3")
    tx = sess.client("transcribe")

    bucket = bucket or os.environ.get("LECTURE_S3_BUCKET") or default_bucket_name(account)
    ensure_bucket(s3, bucket)

    run_id = uuid.uuid4().hex[:12]
    key = f"audio/{run_id}{audio_path.suffix.lower()}"
    job_name = f"lecture-notes-{run_id}"

    log(f"  uploading audio to s3://{bucket}/{key}")
    s3.upload_file(str(audio_path), bucket, key)
    try:
        params = {
            "TranscriptionJobName": job_name,
            "Media": {"MediaFileUri": f"s3://{bucket}/{key}"},
            "MediaFormat": fmt,
        }
        if language == "auto":
            params["IdentifyLanguage"] = True
            # A short list makes identification more accurate than "any language",
            # and lets Indian-accented English come back as en-IN.
            options = [c.strip() for c in LANGUAGE_OPTIONS.split(",") if c.strip()]
            if len(options) >= 2:
                params["LanguageOptions"] = options
        else:
            params["LanguageCode"] = language
        tx.start_transcription_job(**params)
        log(f"  transcription job {job_name} started")

        deadline = time.time() + timeout_seconds
        while True:
            job = tx.get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
            status = job["TranscriptionJobStatus"]
            if status == "COMPLETED":
                break
            if status == "FAILED":
                raise RuntimeError(f"Transcribe failed: {job.get('FailureReason')}")
            if time.time() > deadline:
                raise TimeoutError(f"Transcribe job {job_name} did not finish in time")
            time.sleep(poll_seconds)

        # Without an output bucket, Transcribe returns a short-lived HTTPS link.
        uri = job["Transcript"]["TranscriptFileUri"]
        with urllib.request.urlopen(uri, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        detected = job.get("LanguageCode")
        if detected:
            data.setdefault("detected_language", detected)
        return data
    finally:
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception:  # noqa: BLE001 - cleanup must not hide the real error
            log(f"  warning: could not delete s3://{bucket}/{key}")
        try:
            tx.delete_transcription_job(TranscriptionJobName=job_name)
        except Exception:  # noqa: BLE001
            pass
