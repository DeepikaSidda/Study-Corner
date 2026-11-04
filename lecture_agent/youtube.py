"""YouTube links: check the video with the YouTube Data API, then download it
or use its captions.

Only videos the creator published under YouTube's Creative Commons licence
(CC BY) are downloaded, because that licence lets anyone reuse the video. The
licence is read from the official YouTube Data API (`status.license`), and
checked again from the video page just before downloading. Any other video is
never downloaded: it plays from YouTube's embedded player, and the notes are
made from its English captions (see "captions" below).

The API key comes from YOUTUBE_API_KEY (or .env). It is sent in the
X-Goog-Api-Key header, never in a URL, so it cannot leak into logs or errors.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config

API = "https://www.googleapis.com/youtube/v3/videos"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
HOSTS = {"youtube.com", "m.youtube.com", "www.youtube.com", "music.youtube.com", "youtube-nocookie.com",
         "www.youtube-nocookie.com", "youtu.be", "www.youtu.be"}
MAX_MINUTES = int(os.environ.get("LECTURE_MAX_YOUTUBE_MINUTES", "120"))
MAX_DOWNLOAD_MB = int(os.environ.get("LECTURE_MAX_YOUTUBE_MB", "1500"))


class YouTubeError(ValueError):
    """A problem to show the student as it is."""


@dataclass
class VideoInfo:
    id: str
    url: str
    title: str
    channel: str
    channel_url: str
    seconds: int
    license: str  # "creativeCommon" or "youtube"
    live: bool
    privacy: str
    language: str  # spoken language set by the creator, "" if not set
    thumbnail: str

    def to_dict(self) -> dict:
        return asdict(self)


def enabled() -> bool:
    return bool(config.YOUTUBE_API_KEY)


def parse_video_id(link: str) -> str:
    """The 11-character video id from any normal YouTube link."""
    link = (link or "").strip()
    if VIDEO_ID.match(link):
        return link
    if not link or len(link) > 500:
        raise YouTubeError("Paste a YouTube video link, for example https://www.youtube.com/watch?v=…")
    parsed = urlparse(link if "://" in link else "https://" + link)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in HOSTS:
        raise YouTubeError("That isn't a YouTube link. Paste a link from youtube.com or youtu.be.")
    parts = [p for p in parsed.path.split("/") if p]
    candidate = ""
    if host.endswith("youtu.be"):
        candidate = parts[0] if parts else ""
    elif parts[:1] == ["watch"]:
        candidate = parse_qs(parsed.query).get("v", [""])[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
        candidate = parts[1]
    elif parts[:1] == ["playlist"]:
        raise YouTubeError("That's a playlist. Open one video from it and paste that video's link.")
    if not VIDEO_ID.match(candidate):
        raise YouTubeError("Couldn't find a video in that link. Open the video on YouTube and copy its address.")
    return candidate


def _seconds(iso: str) -> int:
    """ISO 8601 duration ("PT1H5M30S", "P1DT2H") to seconds."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def fetch_info(video_id: str) -> VideoInfo:
    """Title, length, licence and status from the YouTube Data API (1 quota unit)."""
    import httpx

    if not enabled():
        raise YouTubeError("YouTube links are switched off: add YOUTUBE_API_KEY to the .env file.")
    try:
        r = httpx.get(API, timeout=20, headers={"X-Goog-Api-Key": config.YOUTUBE_API_KEY},
                      params={"part": "snippet,contentDetails,status", "id": video_id})
    except httpx.HTTPError as exc:
        raise YouTubeError(f"Couldn't reach YouTube ({type(exc).__name__}). Check the internet connection.") from None
    if r.status_code != 200:
        try:
            error = r.json()["error"]
            reason = (error.get("errors") or [{}])[0].get("reason", "")
            message = error.get("message", "")
        except (ValueError, KeyError):
            reason, message = "", r.text[:200]
        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            raise YouTubeError("The YouTube API key has used up today's free quota. Try again tomorrow.")
        if r.status_code in (400, 403) and ("API key" in message or reason in ("keyInvalid", "forbidden")):
            raise YouTubeError("The YouTube API key was rejected. Check YOUTUBE_API_KEY in the .env file.")
        raise YouTubeError(f"YouTube returned an error ({r.status_code}): {message[:160]}")
    items = r.json().get("items") or []
    if not items:
        raise YouTubeError("That video doesn't exist, or it is private.")
    item = items[0]
    snippet, status = item.get("snippet", {}), item.get("status", {})
    return VideoInfo(
        id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=snippet.get("title", "YouTube video")[:200],
        channel=snippet.get("channelTitle", "")[:120],
        channel_url=f"https://www.youtube.com/channel/{snippet.get('channelId', '')}" if snippet.get("channelId") else "",
        seconds=_seconds(item.get("contentDetails", {}).get("duration", "")),
        license=status.get("license", "youtube"),
        live=snippet.get("liveBroadcastContent", "none") != "none",
        privacy=status.get("privacyStatus", ""),
        language=(snippet.get("defaultAudioLanguage") or "")[:12],
        thumbnail=f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    )


def problems(info: VideoInfo) -> list[str]:
    """Reasons this video can't be used at all, in either mode. Empty means it can."""
    out = []
    if info.live:
        out.append("This is a live stream or a premiere that hasn't finished. Try again once it is a normal video.")
    if info.privacy == "private":
        out.append("This video is private.")
    if info.seconds and info.seconds > MAX_MINUTES * 60:
        out.append(f"This video is {info.seconds // 60} minutes long. The limit is {MAX_MINUTES} minutes.")
    return out


def can_download(info: VideoInfo) -> bool:
    """Only Creative Commons videos in English (or with no language set) are downloaded."""
    english = not info.language or info.language.lower().startswith("en")
    return info.license == "creativeCommon" and english


def check(link: str) -> tuple[VideoInfo, list[str]]:
    info = fetch_info(parse_video_id(link))
    return info, problems(info)


# ======================================================================= captions
# For videos that may not be downloaded, the video plays from YouTube's own
# embedded player and the notes are made from the video's captions: the same
# timed text YouTube shows under "Show transcript". Nothing is downloaded but
# that text. Captions are read with youtube-transcript-api (the approach used
# in Smart-Podcast-Finder); when YouTube doesn't share them, the student can
# paste the transcript instead.

ENGLISH = ("en", "en-US", "en-GB", "en-IN", "en-CA", "en-AU")
MAX_LINE_SECONDS = 14.0
MIN_LINE_SECONDS = 5.0
MAX_PASTE_CHARS = 400_000
_TAG = re.compile(r"\[(?:music|applause|laughter|silence|inaudible|foreign|__)\]|♪+", re.I)


class CaptionsUnavailable(Exception):
    """kind: 'none' (no captions or YouTube refused), 'not_english' (only other languages)."""

    def __init__(self, message: str, kind: str = "none"):
        super().__init__(message)
        self.kind = kind


@dataclass
class Captions:
    language: str
    language_name: str
    generated: bool
    translated: bool
    method: str  # "youtube" or "pasted"
    segments: list  # list[Segment]

    @property
    def words(self) -> int:
        return sum(len(s.text.split()) for s in self.segments)

    def describe(self) -> str:
        if self.method == "pasted":
            return "the pasted transcript"
        kind = "auto-generated" if self.generated else "made by the creator"
        if self.translated:
            kind = "auto-translated to English by YouTube"
        return f"YouTube's English captions ({kind})"

    def summary(self) -> dict:
        return {"language": self.language, "language_name": self.language_name, "generated": self.generated,
                "translated": self.translated, "method": self.method, "lines": len(self.segments),
                "words": self.words, "describe": self.describe()}


def _clean(text: str) -> str:
    text = _TAG.sub(" ", text.replace("\n", " "))
    return re.sub(r"\s+", " ", text).strip()


def merge_snippets(snippets: list[tuple[float, float, str]]) -> list:
    """Join caption fragments (about 3 s each, often unpunctuated) into lines of 5-14 seconds."""
    from .transcript import SENTENCE_END, Segment

    rows = [(start, dur, _clean(text)) for start, dur, text in snippets]
    rows = [r for r in rows if r[2]]
    rows.sort(key=lambda r: r[0])
    segments: list[Segment] = []
    words: list[str] = []
    start = end = None
    for i, (s, dur, text) in enumerate(rows):
        next_start = rows[i + 1][0] if i + 1 < len(rows) else s + dur
        e = min(s + dur, next_start) if next_start > s else s + dur  # captions overlap; stop at the next one
        if words and start is not None and e - start > MAX_LINE_SECONDS:
            segments.append(Segment(0, round(start, 2), round(end, 2), " ".join(words)))  # close before it gets too long
            words, start = [], None
        if start is None:
            start = s
        words.append(text)
        end = e
        length = end - start
        sentence_done = text[-1:] in SENTENCE_END and length >= MIN_LINE_SECONDS
        if sentence_done or length >= MAX_LINE_SECONDS - 1.0:
            segments.append(Segment(0, round(start, 2), round(end, 2), " ".join(words)))
            words, start = [], None
    if words and start is not None:
        segments.append(Segment(0, round(start, 2), round(end, 2), " ".join(words)))
    for n, seg in enumerate(segments, start=1):
        seg.id = n
    return segments


_cache: dict[str, tuple[float, Captions]] = {}


def fetch_captions(video_id: str) -> Captions:
    """English captions for a video, as timed transcript lines (no AWS, no API quota)."""
    import time as _time

    cached = _cache.get(video_id)
    if cached and _time.time() - cached[0] < 1800:
        return cached[1]
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise CaptionsUnavailable("Captions support isn't installed (pip install youtube-transcript-api).") from None

    api = YouTubeTranscriptApi()
    try:
        listing = list(api.list(video_id))
    except Exception as exc:  # noqa: BLE001 - the library raises one type per YouTube refusal
        name = type(exc).__name__
        reason = {
            "TranscriptsDisabled": "the creator turned captions off",
            "RequestBlocked": "YouTube blocked the request from this network",
            "IpBlocked": "YouTube blocked the request from this network",
            "VideoUnavailable": "YouTube says the video is unavailable",
            "AgeRestricted": "the video is age-restricted",
        }.get(name, f"YouTube didn't share them ({name})")
        raise CaptionsUnavailable(f"Couldn't get captions: {reason}.") from None
    if not listing:
        raise CaptionsUnavailable("This video has no captions.")

    def pick(manual: bool):
        for code in ENGLISH:
            for t in listing:
                if t.language_code == code and t.is_generated != manual:
                    return t
        for t in listing:
            if t.language_code.lower().startswith("en") and t.is_generated != manual:
                return t
        return None

    chosen, translated = pick(manual=True) or pick(manual=False), False
    if chosen is None:
        source = next((t for t in listing if t.is_translatable), None)
        if source is None:
            names = ", ".join(sorted({t.language.replace(" (auto-generated)", "") for t in listing}))
            raise CaptionsUnavailable(
                f"This video only has captions in {names}, and YouTube can't translate them to English. "
                "Study Corner works with English lectures only.", kind="not_english")
        chosen, translated = source.translate("en"), True
    try:
        fetched = chosen.fetch()
    except Exception as exc:  # noqa: BLE001
        raise CaptionsUnavailable(f"Couldn't download the captions ({type(exc).__name__}).") from None
    segments = merge_snippets([(s.start, s.duration, s.text) for s in fetched.snippets])
    if sum(len(s.text.split()) for s in segments) < 40:
        raise CaptionsUnavailable("The captions are almost empty, so there isn't enough to make notes from.")
    caps = Captions(language="en" if translated else chosen.language_code,
                    language_name=chosen.language, generated=bool(chosen.is_generated),
                    translated=translated, method="youtube", segments=segments)
    _cache[video_id] = (_time.time(), caps)
    return caps


_STAMP = re.compile(r"^(\d{1,2}(?::\d{2}){1,2})$")
_STAMP_INLINE = re.compile(r"^(\d{1,2}(?::\d{2}){1,2})\s+(.+)$")
_SPOKEN_LENGTH = re.compile(r"^\d+ (?:hours?|minutes?|seconds?)(?:,? \d+ (?:minutes?|seconds?))*$", re.I)


def _to_seconds(stamp: str) -> float:
    total = 0
    for part in stamp.split(":"):
        total = total * 60 + int(part)
    return float(total)


def parse_pasted(text: str, video_seconds: int = 0) -> Captions:
    """A transcript copied from YouTube's "Show transcript" panel, with its times."""
    text = (text or "").replace("\r", "")
    if len(text) > MAX_PASTE_CHARS:
        raise CaptionsUnavailable("That transcript is too long. Paste the transcript of one video.")
    entries: list[list] = []  # [start, text]
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or _SPOKEN_LENGTH.match(line):
            continue  # "4 seconds", "1 minute, 2 seconds": screen-reader text YouTube copies too
        if m := _STAMP.match(line):
            entries.append([_to_seconds(m.group(1)), ""])
        elif m := _STAMP_INLINE.match(line):
            entries.append([_to_seconds(m.group(1)), m.group(2)])
        elif entries:
            entries[-1][1] = f"{entries[-1][1]} {line}".strip()
    entries = [e for e in entries if e[1]]
    if len(entries) < 3:
        raise CaptionsUnavailable("The pasted text has no times in it. On YouTube, open \"Show transcript\", "
                                  "make sure the times are shown, then copy everything and paste it here.")
    letters = re.findall(r"[^\W\d_]", " ".join(e[1] for e in entries))
    latin = sum(1 for ch in letters if ch.isascii())
    if letters and latin / len(letters) < 0.6:
        raise CaptionsUnavailable("The pasted transcript isn't in English. Study Corner works with English "
                                  "lectures only.", kind="not_english")
    snippets = []
    for i, (start, line) in enumerate(entries):
        nxt = entries[i + 1][0] if i + 1 < len(entries) else (video_seconds or start + 5)
        snippets.append((start, max(0.5, min(nxt - start, 30.0)), line))
    segments = merge_snippets(snippets)
    if sum(len(s.text.split()) for s in segments) < 40:
        raise CaptionsUnavailable("The pasted transcript is too short to make notes from.")
    return Captions(language="en", language_name="English", generated=False, translated=False,
                    method="pasted", segments=segments)


def plan(link: str) -> dict:
    """Decide how a link will be handled: download it, use its captions, or ask for a paste."""
    info, blocking = check(link)
    out = {"video": info, "problems": list(blocking), "mode": None, "captions": None, "note": ""}
    if blocking:
        return out
    if can_download(info):
        out["mode"] = "download"
        out["note"] = ("Creative Commons, so the video is downloaded and every feature works, "
                       "including reading the board.")
        return out
    why = ("It has the standard YouTube licence" if info.license != "creativeCommon"
           else f"The creator set the spoken language to '{info.language}'")
    try:
        caps = fetch_captions(info.id)
    except CaptionsUnavailable as exc:
        if exc.kind == "not_english":
            out["problems"].append(str(exc))
            return out
        out["mode"] = "paste"
        out["note"] = (f"{why}, so the video isn't downloaded, and {str(exc)[0].lower()}{str(exc)[1:]} "
                       "Copy the transcript from YouTube and paste it below.")
        return out
    out["mode"] = "captions"
    out["captions"] = caps.summary()
    out["note"] = (f"{why}, so it isn't downloaded. It plays from YouTube on the notes page, and the notes are "
                   f"made from {caps.describe()}: {caps.words:,} words. The board and slides aren't read.")
    return out


def download(info: VideoInfo, folder: Path, log=print) -> Path:
    """Download the video (720p or less, with audio) into folder/lecture.<ext>."""
    import yt_dlp

    from .media import ffmpeg_exe

    folder.mkdir(parents=True, exist_ok=True)
    reported: dict[str, int] = {}

    def progress(d: dict) -> None:
        kind = "audio" if (d.get("info_dict") or {}).get("vcodec") == "none" else "video"
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                pct = int(100 * d.get("downloaded_bytes", 0) / total) // 25 * 25
                if pct > reported.get(kind, -1) and pct < 100:
                    reported[kind] = pct
                    log(f"  download: {kind} {pct}% of {total / 1e6:.0f} MB")
        elif d.get("status") == "finished":
            log(f"  download: {kind} finished")

    class Quiet:
        def debug(self, msg):  # noqa: D401 - yt-dlp logger interface
            pass

        def info(self, msg):
            pass

        def warning(self, msg):
            pass

        def error(self, msg):
            pass

    options = {
        # Prefer MP4 video + M4A audio so browsers can play the result; 720p is
        # enough to read slides and code, and keeps the download small.
        "format": ("bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]"
                   "/bv*[height<=720]+ba/b[height<=720]/b"),
        "merge_output_format": "mp4",
        "outtmpl": str(folder / "lecture.%(ext)s"),
        "noplaylist": True,
        "max_filesize": MAX_DOWNLOAD_MB * 1024 * 1024,
        "ffmpeg_location": ffmpeg_exe(),
        "js_runtimes": {"node": {}, "deno": {}},
        "progress_hooks": [progress],
        "logger": Quiet(),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 3,
        "overwrites": True,
        "writethumbnail": False,
        "writesubtitles": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            meta = ydl.extract_info(info.url, download=False)
            # Check the licence a second time, from the video page itself.
            page_license = (meta.get("license") or "").lower()
            if page_license and "creative commons" not in page_license:
                raise YouTubeError("The video page doesn't show a Creative Commons licence, so it wasn't downloaded.")
            if meta.get("is_live"):
                raise YouTubeError("This video is live right now, so it can't be downloaded.")
            ydl.process_ie_result(meta, download=True)
    except YouTubeError:
        raise
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types; show a short reason
        message = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "")
        raise YouTubeError(f"Downloading from YouTube failed: {message[:240]}") from None

    files = [p for p in folder.glob("lecture.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".m4a")]
    if not files:
        raise YouTubeError("The download finished but no video file was saved.")
    return max(files, key=lambda p: p.stat().st_size)
