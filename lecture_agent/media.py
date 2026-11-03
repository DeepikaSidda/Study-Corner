"""Video and audio handling: duration, audio extraction, slide frames.

Uses the ffmpeg binary bundled with imageio-ffmpeg, so nothing needs to be
installed system-wide.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageChops, ImageFilter


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-y", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{proc.stderr[-1500:]}")
    return proc


def _probe(path: Path) -> str:
    """ffmpeg prints stream info to stderr when given only an input."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stderr


def media_duration(path: Path) -> float:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", _probe(path))
    if not match:
        raise RuntimeError(f"Could not read the duration of {path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def has_video(path: Path) -> bool:
    return bool(re.search(r"Stream #\S+.*: Video:", _probe(path)))


def has_audio(path: Path) -> bool:
    return bool(re.search(r"Stream #\S+.*: Audio:", _probe(path)))


def extract_audio(video: Path, out_path: Path) -> Path:
    """Mono 16 kHz FLAC: small, lossless, and accepted by Amazon Transcribe."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", str(out_path)])
    return out_path


@dataclass
class Frame:
    index: int
    time: float  # seconds from the start; accurate to within one sampling interval
    path: Path


def sample_frames(video: Path, out_dir: Path, every_seconds: float = 10.0, width: int = 1280) -> list[Frame]:
    """Save one frame every `every_seconds` seconds."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.jpg"):
        old.unlink()
    _run(
        [
            "-i", str(video),
            "-vf", f"fps=1/{every_seconds},scale={width}:-2",
            "-q:v", "3",
            str(out_dir / "f_%05d.jpg"),
        ]
    )
    files = sorted(out_dir.glob("f_*.jpg"))
    return [Frame(index=i, time=i * every_seconds, path=p) for i, p in enumerate(files)]


def _thumbnail(path: Path) -> Image.Image:
    # A light blur stops video compression noise counting as change.
    with Image.open(path) as img:
        return img.convert("L").resize((320, 180)).filter(ImageFilter.GaussianBlur(1))


def frame_difference(a: Image.Image, b: Image.Image, pixel_threshold: int = 32) -> float:
    """Share of pixels that changed noticeably, 0 (identical) to 1.

    An average difference over the whole frame does not work: new text on a
    slide changes only a few percent of pixels, so the average stays tiny.
    Measured on the sample lecture: 0.0 between frames of the same slide,
    0.034 to 0.065 at each of the four slide changes.
    """
    changed = ImageChops.difference(a, b).point(lambda v: 255 if v > pixel_threshold else 0)
    return changed.histogram()[255] / float(a.width * a.height)


def slide_changes(
    frames: list[Frame],
    threshold: float = 0.02,
    max_frames: int = 24,
    small_change: float = 0.002,
    max_gap_seconds: float = 30.0,
) -> list[Frame]:
    """Keep only frames where the board or slide visibly changed.

    `threshold` is the share of pixels that must change (default 2%). Each
    frame is compared with the last frame KEPT, not the previous one, so slow
    drift (a teacher writing line by line) still triggers a new frame once
    enough has changed. A teacher walking in front of the board also counts as
    change; `max_frames` caps the cost by thinning frames evenly so the whole
    lecture stays represented.

    Screen recordings of code change very little: typing a line in an editor
    changed under 1% of pixels in a real lesson, so only 3 of 24 frames passed
    the 2% bar. A smaller change (`small_change`) also counts once
    `max_gap_seconds` have passed since the last kept frame. A frame identical
    to the last one is still never kept.
    """
    kept: list[Frame] = []
    last = None
    for frame in frames:
        thumb = _thumbnail(frame.path)
        if last is None:
            keep = True
        else:
            change = frame_difference(thumb, last)
            keep = change >= threshold or (change >= small_change and frame.time - kept[-1].time >= max_gap_seconds)
        if keep:
            kept.append(frame)
            last = thumb
    if len(kept) > max_frames:
        step = len(kept) / max_frames
        kept = [kept[int(i * step)] for i in range(max_frames)]
    return kept
