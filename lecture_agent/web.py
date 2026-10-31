"""Web app: upload a recorded lecture, get notes and a quiz next to the video.

    python -m lecture_agent.web            # http://127.0.0.1:8000

Jobs run one at a time in a background thread, so a second upload waits
instead of doubling the AWS spend. Each job lives in its own folder under
output/web/<job id>/ and survives a server restart.

Binds to 127.0.0.1 by default. There is no login: do not expose it to the
internet as it is, because anyone who can reach it can spend on your account.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import traceback
import uuid
from html import escape
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import config, pipeline

WEB_DIR = Path(os.environ.get("LECTURE_WEB_DIR", "output/web")).resolve()
STATIC = Path(__file__).parent / "web_static"
MAX_UPLOAD_MB = int(os.environ.get("LECTURE_MAX_UPLOAD_MB", "2048"))
VIDEO_TYPES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_TYPES = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
LANGUAGES = {"en-US": "English (US)", "en-IN": "English (India)", "en-GB": "English (UK)",
             "hi-IN": "Hindi", "te-IN": "Telugu", "ta-IN": "Tamil", "auto": "Detect automatically"}
JOB_ID = re.compile(r"^[0-9a-f]{12}$")
FRAME_NAME = re.compile(r"^f_\d{5}\.jpg$")

_lock = threading.Lock()
_queue: "queue.Queue[str]" = queue.Queue()
_jobs: dict[str, dict] = {}
_account: dict = {}


# --------------------------------------------------------------------- jobs
def _job_dir(job_id: str) -> Path:
    return WEB_DIR / job_id


def _save(job: dict) -> None:
    path = _job_dir(job["id"]) / "job.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _update(job_id: str, **changes) -> None:
    with _lock:
        _jobs[job_id].update(changes)
        _save(_jobs[job_id])


def _log(job_id: str, message: str) -> None:
    with _lock:
        job = _jobs[job_id]
        job["log"].append(f"{time.strftime('%H:%M:%S')}  {message}")
        job["log"] = job["log"][-300:]
        _save(job)


def _load_jobs() -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    for path in WEB_DIR.glob("*/job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if job.get("status") in ("queued", "running"):
            job["status"] = "failed"
            job["error"] = "The server stopped before this job finished. Upload it again."
            _save(job)
        _jobs[job["id"]] = job


def _worker() -> None:
    while True:
        job_id = _queue.get()
        job = _jobs[job_id]
        _update(job_id, status="running", started=time.time())
        try:
            opts = job["options"]
            pipeline.run(
                _job_dir(job_id) / job["file"],
                _job_dir(job_id),
                language=opts["language"],
                notes_language=opts["notes_language"],
                questions=opts["questions"],
                log=lambda m, j=job_id: _log(j, m),
            )
            _update(job_id, status="done", finished=time.time())
        except Exception as exc:  # noqa: BLE001 - shown to the user, full trace on the console
            traceback.print_exc()
            _log(job_id, f"ERROR: {exc}")
            _update(job_id, status="failed", finished=time.time(), error=str(exc)[:500])
        finally:
            _queue.task_done()


# --------------------------------------------------------------------- pages
def _page(name: str, **values: str) -> HTMLResponse:
    html = (STATIC / name).read_text(encoding="utf-8")
    for key, value in values.items():
        html = html.replace("{{" + key + "}}", value)
    return HTMLResponse(html)


def _public(job: dict) -> dict:
    keys = ("id", "original_name", "status", "error", "created", "started", "finished", "options", "kind", "log")
    out = {k: job.get(k) for k in keys}
    out["queue_position"] = list(_queued()).index(job["id"]) + 1 if job["status"] == "queued" else None
    return out


def _queued():
    return (j["id"] for j in sorted(_jobs.values(), key=lambda j: j["created"]) if j["status"] == "queued")


async def index(request: Request) -> HTMLResponse:
    rows = []
    for job in sorted(_jobs.values(), key=lambda j: j["created"], reverse=True)[:20]:
        when = time.strftime("%d %b %H:%M", time.localtime(job["created"]))
        rows.append(
            f'<tr><td><a href="/jobs/{job["id"]}">{escape(job["original_name"])}</a></td>'
            f'<td><span class="badge {job["status"]}">{job["status"]}</span></td><td>{when}</td></tr>'
        )
    options = "".join(f'<option value="{k}">{escape(v)}</option>' for k, v in LANGUAGES.items())
    if _account.get("error"):
        account = f'<p class="warn">AWS credentials are not usable: {escape(_account["error"])}</p>'
        disabled = "disabled"
    else:
        account = (f'<p class="account">Runs use AWS account <b>{escape(_account["account"])}</b> '
                   f'in {escape(_account["region"])} with {escape(_account["model"])}.</p>')
        disabled = ""
    return _page(
        "index.html",
        ACCOUNT=account,
        ACCOUNT_ID=escape(_account.get("account", "")),
        DISABLED=disabled,
        LANGUAGE_OPTIONS=options,
        ACCEPT=",".join(sorted(VIDEO_TYPES | AUDIO_TYPES)),
        MAX_MB=str(MAX_UPLOAD_MB),
        JOBS="".join(rows) or '<tr><td colspan="3" class="muted">No lectures yet.</td></tr>',
    )


async def upload(request: Request) -> Response:
    if _account.get("error"):
        return HTMLResponse("AWS credentials are not usable.", status_code=503)
    length = int(request.headers.get("content-length") or 0)
    if length > MAX_UPLOAD_MB * 1024 * 1024:
        return HTMLResponse(f"File too large. The limit is {MAX_UPLOAD_MB} MB.", status_code=413)

    form = await request.form(max_files=1, max_fields=10)
    upload_file = form.get("video")
    if upload_file is None or not getattr(upload_file, "filename", ""):
        return HTMLResponse("Choose a video or audio file.", status_code=400)
    if form.get("consent") != "yes":
        return HTMLResponse("Tick the box to confirm the run may make paid AWS calls.", status_code=400)

    original = Path(upload_file.filename).name
    suffix = Path(original).suffix.lower()
    if suffix not in VIDEO_TYPES | AUDIO_TYPES:
        return HTMLResponse(f"Unsupported file type {escape(suffix)}.", status_code=400)
    language = str(form.get("language") or "en-US")
    if language not in LANGUAGES:
        return HTMLResponse("Unknown language.", status_code=400)
    notes_language = re.sub(r"[^\w \-()]", "", str(form.get("notes_language") or "English"))[:40] or "English"
    try:
        questions = max(3, min(20, int(form.get("questions") or 10)))
    except ValueError:
        questions = 10

    job_id = uuid.uuid4().hex[:12]
    folder = _job_dir(job_id)
    folder.mkdir(parents=True)
    stored = f"lecture{suffix}"
    written = 0
    with (folder / stored).open("wb") as out:
        while chunk := await upload_file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_MB * 1024 * 1024:
                out.close()
                for p in folder.iterdir():
                    p.unlink()
                folder.rmdir()
                return HTMLResponse(f"File too large. The limit is {MAX_UPLOAD_MB} MB.", status_code=413)
            out.write(chunk)
    await upload_file.close()

    job = {
        "id": job_id,
        "original_name": original,
        "file": stored,
        "kind": "video" if suffix in VIDEO_TYPES else "audio",
        "status": "queued",
        "created": time.time(),
        "options": {"language": language, "notes_language": notes_language, "questions": questions},
        "log": [f"{time.strftime('%H:%M:%S')}  uploaded {original} ({written / 1e6:.1f} MB)"],
        "error": None,
    }
    with _lock:
        _jobs[job_id] = job
        _save(job)
    _queue.put(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


def _get_job(request: Request) -> dict | None:
    job_id = request.path_params["job_id"]
    return _jobs.get(job_id) if JOB_ID.match(job_id) else None


async def job_page(request: Request) -> Response:
    job = _get_job(request)
    if job is None:
        return HTMLResponse("Not found", status_code=404)
    return _page("job.html", JOB_ID=job["id"], TITLE=escape(job["original_name"]))


async def job_api(request: Request) -> Response:
    job = _get_job(request)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_public(job))


async def job_file(request: Request) -> Response:
    """Serve only known files from the job folder, never a path from the URL."""
    job = _get_job(request)
    if job is None:
        return Response(status_code=404)
    name = request.path_params["name"]
    folder = _job_dir(job["id"])
    if name == "media":
        return FileResponse(folder / job["file"])  # supports Range, so the player can seek
    if name in ("notes.md", "lecture.json") and (folder / name).exists():
        return FileResponse(folder / name, filename=f"{Path(job['original_name']).stem}-{name}")
    return Response(status_code=404)


async def frame(request: Request) -> Response:
    job = _get_job(request)
    name = request.path_params["name"]
    if job is None or not FRAME_NAME.match(name):
        return Response(status_code=404)
    path = _job_dir(job["id"]) / "frames" / name
    return FileResponse(path) if path.exists() else Response(status_code=404)


async def static(request: Request) -> Response:
    name = request.path_params["name"]
    if name not in ("style.css", "job.js"):
        return Response(status_code=404)
    return FileResponse(STATIC / name)


routes = [
    Route("/", index),
    Route("/upload", upload, methods=["POST"]),
    Route("/jobs/{job_id}", job_page),
    Route("/api/jobs/{job_id}", job_api),
    Route("/jobs/{job_id}/files/{name}", job_file),
    Route("/jobs/{job_id}/frames/{name}", frame),
    Route("/static/{name}", static),
]


def create_app() -> Starlette:
    try:
        _account.update(config.whoami())
    except Exception as exc:  # noqa: BLE001
        _account["error"] = str(exc)
    _load_jobs()
    threading.Thread(target=_worker, daemon=True, name="lecture-worker").start()
    return Starlette(routes=routes)


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Lecture Notes Agent web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    app = create_app()
    print(f"AWS account: {_account.get('account') or _account.get('error')}")
    print(f"Open http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
