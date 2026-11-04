"""Translate timed transcript lines to English with Amazon Translate.

Used for YouTube videos whose only captions are in another language (for
example Hindi) that YouTube itself can't translate. Each line is translated
on its own, so its start and end times stay exactly as they were, and every
later step (notes, quiz checks, chat citations) works on the English lines.

Cost: Amazon Translate charges per character (about $15 per million). A
15-minute Hindi lecture is roughly 15,000-20,000 characters.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from . import config
from .transcript import Segment

MAX_BYTES = 9000  # TranslateText allows 10,000 bytes per request
WORKERS = 6


def _translate_one(client, text: str, source: str) -> str:
    data = text.encode("utf-8")
    if len(data) > MAX_BYTES:  # very long line: cut at a word boundary
        text = data[:MAX_BYTES].decode("utf-8", "ignore").rsplit(" ", 1)[0]
    for attempt in range(4):
        try:
            r = client.translate_text(Text=text, SourceLanguageCode=source, TargetLanguageCode="en")
            return r["TranslatedText"].strip()
        except Exception as exc:  # noqa: BLE001 - retry throttling, re-raise the rest
            name = type(exc).__name__
            if attempt == 3 or not any(k in f"{name} {exc}" for k in ("Throttl", "TooManyRequests", "ServiceUnavailable")):
                raise
            time.sleep(1.5 * (attempt + 1))
    return text


def to_english(segments: list[Segment], source_language: str, log=print) -> list[Segment]:
    """English copies of the segments, same ids and times. source_language: e.g. "hi", or "auto"."""
    source = (source_language or "auto").split("-")[0] or "auto"
    client = config.session().client("translate")
    chars = sum(len(s.text) for s in segments)
    log(f"translate: {len(segments)} lines ({chars:,} characters) from '{source}' to English with Amazon Translate")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        texts = list(pool.map(lambda s: _translate_one(client, s.text, source), segments))
    out = [Segment(s.id, s.start, s.end, t or s.text) for s, t in zip(segments, texts)]
    log(f"translate: done, {sum(len(t.split()) for t in texts):,} English words")
    return out
