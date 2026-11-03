"""YouTube links: check the video with the YouTube Data API, then download it.

Only videos the creator published under YouTube's Creative Commons licence
(CC BY) are accepted, because that licence lets anyone reuse the video. The
licence is read from the official YouTube Data API (`status.license`), and
checked again from the video page just before downloading.

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
    """Reasons this video can't be used. Empty means it can."""
    out = []
    if info.license != "creativeCommon":
        out.append("This video has the standard YouTube licence, so Study Corner isn't allowed to download it. "
                   "Only videos the creator marked Creative Commons (CC BY) can be used. "
                   "You can download your own copy and upload the file instead, if you have the right to.")
    if info.live:
        out.append("This is a live stream or a premiere that hasn't finished. Try again once it is a normal video.")
    if info.privacy == "private":
        out.append("This video is private.")
    if info.seconds and info.seconds > MAX_MINUTES * 60:
        out.append(f"This video is {info.seconds // 60} minutes long. The limit is {MAX_MINUTES} minutes.")
    if info.language and not info.language.lower().startswith("en"):
        out.append(f"The creator set the spoken language to '{info.language}'. Study Corner only works with "
                   "lectures in English.")
    return out


def check(link: str) -> tuple[VideoInfo, list[str]]:
    info = fetch_info(parse_video_id(link))
    return info, problems(info)


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
