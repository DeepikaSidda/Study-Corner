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
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import chat, config, pipeline

WEB_DIR = Path(os.environ.get("LECTURE_WEB_DIR", "output/web")).resolve()
STATIC = Path(__file__).parent / "web_static"
MAX_UPLOAD_MB = int(os.environ.get("LECTURE_MAX_UPLOAD_MB", "2048"))
VIDEO_TYPES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_TYPES = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
LECTURE_LANGUAGE = "en-US"  # Amazon Transcribe language code
NOTES_LANGUAGE = "English"
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


_summaries: dict[str, tuple[float, dict]] = {}


def _summary(job: dict) -> dict:
    """Title, length, question count and thumbnail of a finished lecture.

    Read from lecture.json and cached until that file changes.
    """
    path = _job_dir(job["id"]) / "lecture.json"
    if job["status"] != "done" or not path.exists():
        return {}
    mtime = path.stat().st_mtime
    cached = _summaries.get(job["id"])
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    boards = data.get("board") or []
    frame = boards[0]["frame"] if boards else None
    if frame is None:
        frames = sorted((_job_dir(job["id"]) / "frames").glob("f_*.jpg"))
        frame = frames[0].name if frames else None
    summary = {
        "title": data["notes"]["title"],
        "seconds": data.get("metrics", {}).get("lecture_seconds"),
        "questions": len(data.get("quiz", [])),
        "frame": frame if frame and FRAME_NAME.match(frame) else None,
    }
    _summaries[job["id"]] = (mtime, summary)
    return summary


def _lecture_card(job: dict) -> str:
    from .transcript import fmt_time

    info = _summary(job)
    status = escape(job["status"])
    when = time.strftime("%d %b, %H:%M", time.localtime(job["created"]))
    title = escape(info.get("title") or job["original_name"])
    if info.get("frame"):
        thumb = f'<img src="/jobs/{job["id"]}/frames/{info["frame"]}" alt="" loading="lazy">'
    else:
        thumb = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">'
                 '<rect x="3" y="5" width="14" height="14" rx="2"/><path d="m17 10 4-2v8l-4-2"/></svg>')
    duration = f'<span class="dur">{fmt_time(info["seconds"])}</span>' if info.get("seconds") else ""
    parts = [when]
    if info.get("questions"):
        parts.append(f"{info['questions']} questions")
    if info.get("title"):
        parts.append(escape(job["original_name"]))
    return (
        f'<a class="lecture-card" href="/jobs/{job["id"]}">'
        f'<span class="thumb">{thumb}<span class="badge {status}">{status}</span>{duration}</span>'
        f'<span class="lc-body"><b>{title}</b><span class="lc-meta">{" · ".join(parts)}</span></span></a>'
    )


async def index(request: Request) -> HTMLResponse:
    jobs = sorted(_jobs.values(), key=lambda j: j["created"], reverse=True)[:12]
    rows = [_lecture_card(job) for job in jobs]
    # Only shown when something is wrong; the account and model are printed
    # in the terminal when the server starts.
    if _account.get("error"):
        account = f'<p class="alert">AWS credentials are not usable: {escape(_account["error"])}</p>'
        disabled = "disabled"
    else:
        account = ""
        disabled = ""
    return _page(
        "index.html",
        ACCOUNT=account,
        DISABLED=disabled,
        ACCEPT=",".join(sorted(VIDEO_TYPES | AUDIO_TYPES)),
        MAX_MB=str(MAX_UPLOAD_MB),
        JOBS="".join(rows) or (
            '<div class="empty" style="grid-column:1/-1"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.6"><rect x="3" y="5" width="14" height="14" rx="2"/><path d="m17 10 4-2v8l-4-2"/></svg>'
            'No lectures yet. Upload your first one above.</div>'
        ),
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

    original = Path(upload_file.filename).name
    suffix = Path(original).suffix.lower()
    if suffix not in VIDEO_TYPES | AUDIO_TYPES:
        return HTMLResponse(f"Unsupported file type {escape(suffix)}.", status_code=400)
    # The web app is English only: lectures are transcribed as English and the
    # notes, quiz and chat are written in English. Any language fields sent
    # with the form are ignored.
    language, notes_language = LECTURE_LANGUAGE, NOTES_LANGUAGE
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
    if name == "lecture.json" and (folder / name).exists():
        return FileResponse(folder / name)  # read by the page itself
    if name == "notes.pdf" and job["status"] == "done":
        pdf_path = await run_in_threadpool(_notes_pdf, folder)
        filename = f"{Path(job['original_name']).stem}-notes.pdf"
        return FileResponse(pdf_path, media_type="application/pdf", filename=filename)
    return Response(status_code=404)


def _notes_pdf(folder: Path) -> Path:
    """Build the PDF on first request; rebuild only if lecture.json changed."""
    from .pdf import build_pdf

    source, target = folder / "lecture.json", folder / "notes.pdf"
    if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
        build_pdf(json.loads(source.read_text(encoding="utf-8")), target)
    return target


async def notes_page(request: Request) -> Response:
    """The full notes as one readable page, with Copy, Print and PDF buttons."""
    from .render import render_document_html

    job = _get_job(request)
    if job is None or job["status"] != "done":
        return HTMLResponse("Not found", status_code=404)
    data = json.loads((_job_dir(job["id"]) / "lecture.json").read_text(encoding="utf-8"))
    return _page("notes.html", JOB_ID=job["id"], TITLE=escape(data["notes"]["title"]),
                 DOC=render_document_html(data))


async def frame(request: Request) -> Response:
    job = _get_job(request)
    name = request.path_params["name"]
    if job is None or not FRAME_NAME.match(name):
        return Response(status_code=404)
    path = _job_dir(job["id"]) / "frames" / name
    return FileResponse(path) if path.exists() else Response(status_code=404)


_chat_locks: dict[tuple[str, str], threading.Lock] = {}


def _chat_lock(job_id: str, student: str) -> threading.Lock:
    # One question at a time per student, so two tabs cannot write the same
    # session files at once.
    with _lock:
        return _chat_locks.setdefault((job_id, student), threading.Lock())


async def chat_api(request: Request) -> Response:
    """GET: the student's saved conversation. POST: ask a question."""
    job = _get_job(request)
    if job is None or job["status"] != "done":
        return JSONResponse({"error": "This lecture is not ready yet."}, status_code=404)
    folder = _job_dir(job["id"])
    language = job["options"]["notes_language"]

    if request.method == "GET":
        student = request.query_params.get("student", "")
        if not chat.STUDENT_ID.match(student):
            return JSONResponse({"error": "invalid student id"}, status_code=400)
        return JSONResponse({"turns": await run_in_threadpool(chat.history, folder, student, language)})

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "Send JSON."}, status_code=400)
    student = str(body.get("student", ""))
    question = str(body.get("message", "")).strip()
    if not chat.STUDENT_ID.match(student):
        return JSONResponse({"error": "invalid student id"}, status_code=400)
    if not question or len(question) > chat.MAX_QUESTION_CHARS:
        return JSONResponse({"error": f"Ask a question of 1 to {chat.MAX_QUESTION_CHARS} characters."}, status_code=400)

    def answer():
        with _chat_lock(job["id"], student):
            return chat.ask(folder, student, question, language)

    try:
        result = await run_in_threadpool(answer)
    except Exception as exc:  # noqa: BLE001 - shown to the student, trace on the console
        traceback.print_exc()
        return JSONResponse({"error": f"Could not answer: {str(exc)[:200]}"}, status_code=500)
    return JSONResponse(result)


async def static(request: Request) -> Response:
    name = request.path_params["name"]
    if name not in ("style.css", "job.js", "app.js", "notes.js"):
        return Response(status_code=404)
    return FileResponse(STATIC / name)


routes = [
    Route("/", index),
    Route("/upload", upload, methods=["POST"]),
    Route("/jobs/{job_id}", job_page),
    Route("/jobs/{job_id}/notes", notes_page),
    Route("/api/jobs/{job_id}", job_api),
    Route("/api/jobs/{job_id}/chat", chat_api, methods=["GET", "POST"]),
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

    parser = argparse.ArgumentParser(description="Study Corner web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    app = create_app()
    print(f"AWS account: {_account.get('account') or _account.get('error')}")
    print(f"Open http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
