"""Transcript segments with timestamps, parsed from Amazon Transcribe output.

Every later step refers to transcript lines by segment id (S1, S2, ...). That
id is what lets the checker verify a quiz answer against the exact words the
teacher said, and what turns into a "rewatch from 03:14" link.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

SENTENCE_END = {".", "?", "!", "।"}  # includes the Devanagari danda


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str


def fmt_time(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def parse_transcribe_json(data: dict, max_segment_seconds: float = 15.0) -> list[Segment]:
    """Turn an Amazon Transcribe result into numbered, timestamped segments.

    Built from the word-level `items` when present, so lines are short enough
    for every chapter and key point to get its own time. Transcribe's own
    `audio_segments` are often 20+ seconds for unscripted speech (a real
    4-minute lesson came back as 13 lines), which gave two chapters the same
    start time. `audio_segments` is only used when there are no word items.
    """
    results = data.get("results", data)
    raw = results.get("audio_segments") or []
    items = results.get("items") or []
    segments: list[Segment] = []

    if items:
        segments = _segments_from_items(items, max_segment_seconds)
    elif raw:
        for seg in raw:
            text = (seg.get("transcript") or "").strip()
            if text:
                segments.append(
                    Segment(0, float(seg["start_time"]), float(seg["end_time"]), text)
                )

    for number, seg in enumerate(segments, start=1):
        seg.id = number
    return segments


CLAUSE_END = {",", ";", ":"}
SOFT_SECONDS = 7.0  # after this long, a comma also ends the line


def _segments_from_items(items: list[dict], max_seconds: float) -> list[Segment]:
    segments: list[Segment] = []
    words: list[str] = []
    start = end = None

    for item in items:
        content = item["alternatives"][0]["content"]
        if item.get("type") == "pronunciation":
            word_start = float(item["start_time"])
            word_end = float(item["end_time"])
            # Very long sentences are split so timestamps stay useful.
            if words and start is not None and word_start - start > max_seconds:
                segments.append(Segment(0, start, end, " ".join(words)))
                words, start = [], None
            if start is None:
                start = word_start
            words.append(content)
            end = word_end
        else:  # punctuation attaches to the previous word
            if words:
                words[-1] += content
            long_enough = start is not None and end is not None and end - start >= SOFT_SECONDS
            if words and (content in SENTENCE_END or (content in CLAUSE_END and long_enough)):
                segments.append(Segment(0, start, end, " ".join(words)))
                words, start = [], None

    if words and start is not None:
        segments.append(Segment(0, start, end, " ".join(words)))
    return segments


def format_for_prompt(segments: list[Segment]) -> str:
    return "\n".join(f"[S{s.id} {fmt_time(s.start)}] {s.text}" for s in segments)


def save_segments(segments: list[Segment], path: Path) -> None:
    path.write_text(json.dumps([asdict(s) for s in segments], indent=2, ensure_ascii=False), encoding="utf-8")
