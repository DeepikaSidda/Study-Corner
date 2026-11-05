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

from . import chat, config, db, pipeline, youtube

WEB_DIR = Path(os.environ.get("LECTURE_WEB_DIR", "output/web")).resolve()
STATIC = Path(__file__).parent / "web_static"
MAX_UPLOAD_MB = int(os.environ.get("LECTURE_MAX_UPLOAD_MB", "2048"))
VIDEO_TYPES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_TYPES = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
LECTURE_LANGUAGE = "auto"  # Transcribe identifies the language; non-English is translated to English
NOTES_LANGUAGE = "English"
JOB_ID = re.compile(r"^[0-9a-f]{12}$")

# Demo mode, for a public server: set LECTURE_DEMO_PASSCODE. Everyone can open
# the finished lectures; adding a lecture (upload or YouTube) needs the
# passcode, and chat questions are capped so a public link can't run up the
# AWS bill.
DEMO_PASSCODE = os.environ.get("LECTURE_DEMO_PASSCODE") or None
CHAT_PER_VISITOR = int(os.environ.get("LECTURE_CHAT_PER_VISITOR_PER_DAY", "20"))
CHAT_PER_DAY = int(os.environ.get("LECTURE_CHAT_PER_DAY", "200"))
_chat_counts: dict[str, int] = {}
_chat_count_lock = threading.Lock()


def _passcode_ok(value) -> bool:
    import hmac

    return DEMO_PASSCODE is None or hmac.compare_digest(str(value or "").encode(), DEMO_PASSCODE.encode())


LOCKED = "Adding lectures on this demo needs the passcode from the submission form. The finished lectures below are open to everyone."


def _client_ip(request: Request) -> str:
    host = request.client.host if request.client else "?"
    if host in ("127.0.0.1", "::1"):  # behind the local HTTPS proxy
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return host


def _chat_allowed(ip: str, student: str) -> str | None:
    """None if this chat question may run, else the reason it can't (demo mode only)."""
    if DEMO_PASSCODE is None:
        return None
    day = time.strftime("%Y-%m-%d")
    keys = (f"{day}|all", f"{day}|ip|{ip}", f"{day}|student|{student}")
    with _chat_count_lock:
        if _chat_counts.get(keys[0], 0) >= CHAT_PER_DAY:
            return "The demo has answered its daily number of questions. Please try again tomorrow."
        if max(_chat_counts.get(keys[1], 0), _chat_counts.get(keys[2], 0)) >= CHAT_PER_VISITOR:
            return f"This demo allows {CHAT_PER_VISITOR} questions per visitor per day."
        for k in keys:
            _chat_counts[k] = _chat_counts.get(k, 0) + 1
        for k in [k for k in _chat_counts if not k.startswith(day)]:
            del _chat_counts[k]
    return None
FRAME_NAME = re.compile(r"^f_\d{5}\.jpg$")

_lock = threading.Lock()
_queue: "queue.Queue[str]" = queue.Queue()
_jobs: dict[str, dict] = {}
_account: dict = {}


# --------------------------------------------------------------------- jobs
def _job_dir(job_id: str) -> Path:
    return WEB_DIR / job_id


def _save(job: dict) -> None:
    """Write job.json safely. On Windows another program (antivirus, search
    indexer, an editor) can hold the file open for a moment, and replacing it
    then fails with "Access is denied". Retry, and never let a failed save
    stop the lecture: the job is kept in memory and saved on the next change."""
    path = _job_dir(job["id"]) / "job.json"
    tmp = path.with_suffix(".tmp")
    text = json.dumps(job, indent=2, ensure_ascii=False)
    for attempt in range(8):
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.15 * (attempt + 1))
    try:
        path.write_text(text, encoding="utf-8")  # last resort: write in place
    except OSError as exc:
        print(f"warning: could not save {path}: {exc}")


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
            source = job.get("source") or {}
            if source.get("mode") in ("captions", "pasted"):
                # Never downloaded: notes come from the timed captions saved at submit.
                from .transcript import Segment, save_segments

                folder = _job_dir(job_id)
                rows = json.loads((folder / "captions.json").read_text(encoding="utf-8"))
                lines = [Segment(**r) for r in rows]
                caps = source.get("captions") or {}
                if caps.get("needs_translation"):
                    english = folder / "captions.en.json"
                    if english.exists():  # a rerun: don't pay for the translation twice
                        lines = [Segment(**r) for r in json.loads(english.read_text(encoding="utf-8"))]
                        _log(job_id, "translate: reusing the saved English translation")
                    else:
                        from . import translate

                        lines = translate.to_english(lines, caps.get("language", "auto"),
                                                     log=lambda m, j=job_id: _log(j, m))
                        save_segments(lines, english)
                opts = job["options"]
                pipeline.run(
                    None,
                    folder,
                    segments=lines,
                    lecture_seconds=float(source["video"].get("seconds") or 0) or None,
                    transcript_label=source.get("captions", {}).get("describe", "the captions"),
                    notes_language=opts["notes_language"],
                    questions=opts["questions"],
                    log=lambda m, j=job_id: _log(j, m),
                )
                _update(job_id, status="done", finished=time.time())
                continue
            if source.get("type") == "youtube" and not job.get("file"):
                _log(job_id, "download: from YouTube")
                video = youtube.VideoInfo(**source["video"])
                path = youtube.download(video, _job_dir(job_id), log=lambda m, j=job_id: _log(j, m))
                _update(job_id, file=path.name)
                _log(job_id, f"download: saved {path.name} ({path.stat().st_size / 1e6:.1f} MB)")
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
            _store(job_id)
            _queue.task_done()


def _store(job_id: str) -> None:
    """Save the finished (or failed) lecture to DynamoDB. Never fails the job."""
    job = _jobs[job_id]
    lecture = _job_dir(job_id) / "lecture.json"
    data = None
    if job["status"] == "done" and lecture.exists():
        data = json.loads(lecture.read_text(encoding="utf-8"))
    written = db.safe(db.save_lecture, job, data, log=lambda m: _log(job_id, m))
    if written:
        _log(job_id, f"database: saved to Amazon DynamoDB ({written} items)")


def _backfill() -> None:
    """Copy lectures made before DynamoDB was added (or while it was unreachable)."""
    stored = db.safe(lambda: {m["id"] for m in db.list_lectures(500)}, log=print)
    if stored is None:
        return
    for job in sorted(_jobs.values(), key=lambda j: j["created"]):
        if job["id"] in stored or job["status"] not in ("done", "failed"):
            continue
        lecture = _job_dir(job["id"]) / "lecture.json"
        data = json.loads(lecture.read_text(encoding="utf-8")) if lecture.exists() and job["status"] == "done" else None
        written = db.safe(db.save_lecture, job, data, log=print)
        if written:
            print(f"database: copied {job['id']} ({written} items)")


# --------------------------------------------------------------------- pages
def _page(name: str, **values: str) -> HTMLResponse:
    html = (STATIC / name).read_text(encoding="utf-8")
    for key, value in values.items():
        html = html.replace("{{" + key + "}}", value)
    return HTMLResponse(html)


def _public(job: dict) -> dict:
    keys = ("id", "original_name", "status", "error", "created", "started", "finished", "options", "kind", "log",
            "source")
    out = {k: job.get(k) for k in keys}
    # The S3 bucket name contains the AWS account number; keep it off the page.
    out["log"] = [re.sub(r"s3://\S+", "s3://(private bucket)", line) for line in job.get("log", [])]
    if out.get("error"):
        out["error"] = re.sub(r"\b\d{12}\b", "(account)", out["error"])
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
    with_content = [b for b in boards if b.get("has_content")]
    frame = (with_content or boards)[0]["frame"] if boards else None
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
    video = (job.get("source") or {}).get("video") or {}
    if info.get("frame"):
        thumb = f'<img src="/jobs/{job["id"]}/frames/{info["frame"]}" alt="" loading="lazy">'
    elif youtube.VIDEO_ID.match(video.get("id", "")):
        thumb = f'<img src="https://i.ytimg.com/vi/{video["id"]}/mqdefault.jpg" alt="" loading="lazy">'
    else:
        thumb = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">'
                 '<rect x="3" y="5" width="14" height="14" rx="2"/><path d="m17 10 4-2v8l-4-2"/></svg>')
    duration = f'<span class="dur">{fmt_time(info["seconds"])}</span>' if info.get("seconds") else ""
    parts = [when]
    if info.get("questions"):
        parts.append(f"{info['questions']} questions")
    if video:
        parts.append("YouTube · " + escape(video.get("channel", "")))
    elif info.get("title"):
        parts.append(escape(job["original_name"]))
    if not info.get("seconds") and video.get("seconds"):
        duration = f'<span class="dur">{fmt_time(video["seconds"])}</span>'
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
    yt_on = youtube.enabled()
    return _page(
        "index.html",
        ACCOUNT=account,
        DEMO=(
            '<label class="demo-pass"><span>Demo: adding a lecture needs the passcode from the submission form. '
            'The finished lectures below are open to everyone.</span>'
            '<input type="password" id="passcode" placeholder="Passcode" autocomplete="off" maxlength="64"></label>'
        ) if DEMO_PASSCODE else "",
        DISABLED=disabled,
        YT_DISABLED="" if yt_on else "disabled",
        YT_NOTE="" if yt_on else "YouTube links are switched off. Add YOUTUBE_API_KEY to the .env file to turn them on.",
        YT_BUTTON_OFF="true" if disabled else "false",
        YT_MAX_MIN=str(youtube.MAX_MINUTES),
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
    # Checked before the body is read, so a public demo never stores a stranger's upload.
    if not _passcode_ok(request.headers.get("x-passcode")):
        return HTMLResponse(LOCKED, status_code=403)
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


def _existing_youtube_job(video_id: str) -> dict | None:
    """A job already made (or being made) from this video, so it isn't paid for twice."""
    for job in sorted(_jobs.values(), key=lambda j: j["created"], reverse=True):
        if ((job.get("source") or {}).get("video") or {}).get("id") == video_id and \
                job["status"] in ("queued", "running", "done"):
            return job
    return None


async def youtube_check(request: Request) -> Response:
    """Look a link up before starting: title, length, licence and any reason it can't be used."""
    if not youtube.enabled():
        return JSONResponse({"error": "YouTube links are switched off: add YOUTUBE_API_KEY to the .env file."},
                            status_code=503)
    if not _passcode_ok(request.headers.get("x-passcode")):
        return JSONResponse({"error": LOCKED}, status_code=403)
    try:
        plan = await run_in_threadpool(youtube.plan, request.query_params.get("url", ""))
    except youtube.YouTubeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    info = plan["video"]
    existing = _existing_youtube_job(info.id)
    return JSONResponse({
        "video": info.to_dict(),
        "problems": plan["problems"],
        "mode": plan["mode"],  # "download", "captions", "paste" or None
        "note": plan["note"],
        "captions": plan["captions"],
        "existing_job": existing["id"] if existing else None,
        "max_minutes": youtube.MAX_MINUTES,
    })


async def youtube_submit(request: Request) -> Response:
    """Start a job from a YouTube link. The download happens in the worker, in turn."""
    if _account.get("error"):
        return HTMLResponse("AWS credentials are not usable.", status_code=503)
    if not youtube.enabled():
        return HTMLResponse("YouTube links are switched off: add YOUTUBE_API_KEY to the .env file.", status_code=503)
    if not _passcode_ok(request.headers.get("x-passcode")):
        return HTMLResponse(LOCKED, status_code=403)
    form = await request.form(max_files=1, max_fields=5)
    try:
        questions = max(3, min(20, int(form.get("questions") or 10)))
    except ValueError:
        questions = 10
    # Checked again here: never trust the earlier preview.
    try:
        plan = await run_in_threadpool(youtube.plan, str(form.get("url") or ""))
    except youtube.YouTubeError as exc:
        return HTMLResponse(str(exc), status_code=400)
    if plan["problems"]:
        return HTMLResponse(" ".join(plan["problems"]), status_code=400)
    info = plan["video"]
    existing = _existing_youtube_job(info.id)
    if existing:
        return RedirectResponse(f"/jobs/{existing['id']}", status_code=303)

    from .transcript import fmt_time, save_segments

    # Which transcript: a download (CC), YouTube's captions, or what the student pasted.
    pasted = str(form.get("transcript") or "").strip()
    captions = None
    if plan["mode"] == "download":
        mode, how = "download", "Creative Commons"
    elif pasted:
        try:
            captions = await run_in_threadpool(youtube.parse_pasted, pasted, info.seconds)
        except youtube.CaptionsUnavailable as exc:
            return HTMLResponse(str(exc), status_code=400)
        mode, how = "pasted", "pasted transcript"
    elif plan["mode"] == "captions":
        try:
            captions = await run_in_threadpool(youtube.fetch_captions, info.id)
        except youtube.CaptionsUnavailable as exc:
            return HTMLResponse(f"{exc} Paste the transcript instead.", status_code=400)
        mode, how = "captions", "YouTube captions"
    else:
        return HTMLResponse("YouTube didn't share captions for this video. Paste the transcript from YouTube "
                            "(\"Show transcript\") into the box, then try again.", status_code=400)

    job_id = uuid.uuid4().hex[:12]
    _job_dir(job_id).mkdir(parents=True)
    source = {"type": "youtube", "mode": mode, "video": info.to_dict()}
    if captions is not None:
        save_segments(captions.segments, _job_dir(job_id) / "captions.json")
        source["captions"] = captions.summary()
    job = {
        "id": job_id,
        "original_name": info.title,
        "file": None,  # set by the worker after a download; stays None for captions
        "kind": "video" if mode == "download" else "youtube",
        "source": source,
        "status": "queued",
        "created": time.time(),
        "options": {"language": LECTURE_LANGUAGE, "notes_language": NOTES_LANGUAGE, "questions": questions},
        "log": [f"{time.strftime('%H:%M:%S')}  YouTube link: {info.title} by {info.channel} "
                f"({fmt_time(info.seconds)}, {how})"],
        "error": None,
    }
    with _lock:
        _jobs[job_id] = job
        _save(job)
    _queue.put(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


async def search_api(request: Request) -> Response:
    """Find lectures that mention a word, from Amazon DynamoDB."""
    query = (request.query_params.get("q") or "").strip()[:80]
    if len(query) < 2:
        return JSONResponse({"results": []})
    if not db.ENABLED:
        return JSONResponse({"error": "Search needs the database (LECTURE_DDB is off)."}, status_code=503)
    results = await run_in_threadpool(db.safe, db.search, query)
    if results is None:
        return JSONResponse({"error": "The database couldn't be reached. Try again."}, status_code=503)
    for r in results:  # only lectures this server knows about, with only the fields the page needs
        r["lecture"] = {k: r["lecture"].get(k) for k in ("id", "title", "channel", "seconds", "source_type", "youtube_id")}
    return JSONResponse({"results": [r for r in results if r["lecture"]["id"] in _jobs]})


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
        if not job.get("file") or not (folder / job["file"]).exists():
            return Response(status_code=404)  # a YouTube job before its download
        return FileResponse(folder / job["file"])  # supports Range, so the player can seek
    if name == "lecture.json" and job["status"] == "done":
        stored = await run_in_threadpool(db.safe, db.get_lecture, job["id"])
        if stored:  # read from Amazon DynamoDB; the file is the fallback
            return JSONResponse(stored, headers={"X-Study-Corner-Source": "dynamodb"})
    if name == "lecture.json" and (folder / name).exists():
        return FileResponse(folder / name)  # read by the page itself
    if name == "notes.pdf" and job["status"] == "done":
        pdf_path = await run_in_threadpool(_notes_pdf, job)
        stem = job["original_name"] if job.get("source") else Path(job["original_name"]).stem
        stem = re.sub(r"[^\w\- ]+", "", stem).strip()[:80] or "lecture"
        filename = f"{stem}-notes.pdf"
        return FileResponse(pdf_path, media_type="application/pdf", filename=filename)
    return Response(status_code=404)


def _credit(job: dict) -> str:
    """Attribution line for a YouTube video (CC BY asks for title, creator, link and licence)."""
    source = job.get("source") or {}
    video = source.get("video")
    if not video:
        return ""
    if source.get("mode", "download") == "download":
        return (f'From YouTube: "{video["title"]}" by {video["channel"]}, {video["url"]}, '
                "licensed CC BY (https://creativecommons.org/licenses/by/3.0/). Notes made by Study Corner.")
    made_from = (source.get("captions") or {}).get("describe", "its captions")
    return (f'Video: "{video["title"]}" by {video["channel"]}, {video["url"]}. The video belongs to its '
            f"creator and was not downloaded. Notes made by Study Corner from {made_from}.")


def _lecture_data(job: dict) -> dict:
    data = db.safe(db.get_lecture, job["id"]) or json.loads(
        (_job_dir(job["id"]) / "lecture.json").read_text(encoding="utf-8"))
    if _credit(job):
        data["credit"] = _credit(job)
    return data


def _notes_pdf(job: dict) -> Path:
    """Build the PDF on first request; rebuild only if lecture.json changed."""
    from .pdf import build_pdf

    folder = _job_dir(job["id"])
    source, target = folder / "lecture.json", folder / "notes.pdf"
    if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
        build_pdf(_lecture_data(job), target)
    return target


async def notes_page(request: Request) -> Response:
    """The full notes as one readable page, with Copy, Print and PDF buttons."""
    from .render import render_document_html

    job = _get_job(request)
    if job is None or job["status"] != "done":
        return HTMLResponse("Not found", status_code=404)
    data = _lecture_data(job)
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
    refused = _chat_allowed(_client_ip(request), student)
    if refused:
        return JSONResponse({"error": refused}, status_code=429)

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
    Route("/youtube", youtube_submit, methods=["POST"]),
    Route("/api/youtube", youtube_check),
    Route("/api/search", search_api),
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
    if db.ENABLED and not _account.get("error"):
        threading.Thread(target=_backfill, daemon=True, name="db-backfill").start()
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
