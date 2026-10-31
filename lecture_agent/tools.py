"""Strands tools that let an agent look things up in one lecture.

The tools are built per lecture by `lecture_tools(index)`, so an agent can
only ever read the lecture it was given. Every transcript line a tool returns
is recorded in `index.retrieved`. The chat agent's citation check uses that
record: an answer may only cite times of lines the agent actually read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from strands import tool

from .checker import normalize
from .transcript import Segment, fmt_time

STOPWORDS = set(
    "a an and are as at be by did do does for from how in is it its of on or that the this to was "
    "were what when where which who why will with about can say said teacher lecture".split()
)
MAX_READ = 40


@dataclass
class LectureIndex:
    segments: list[Segment]
    title: str = ""
    sections: list[dict] = field(default_factory=list)  # {"title", "segment_ids"}
    boards: list[dict] = field(default_factory=list)  # {"time", "text", "formulas", "diagram"}
    retrieved: set[int] = field(default_factory=set)
    # Every time label a tool has shown the agent: transcript line starts and
    # board times. The citation check allows exactly these.
    shown_times: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.by_id = {s.id: s for s in self.segments}
        self._words = {s.id: set(normalize(s.text).split()) for s in self.segments}

    @classmethod
    def from_lecture_json(cls, path: Path) -> "LectureIndex":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            segments=[Segment(**s) for s in data["segments"]],
            title=data["notes"]["title"],
            sections=[{"title": s["title"], "segment_ids": s["segment_ids"]} for s in data["notes"]["sections"]],
            boards=[b for b in data.get("board", []) if b.get("has_content")],
        )

    def line(self, seg: Segment) -> str:
        return f"[S{seg.id} {fmt_time(seg.start)}] {seg.text}"

    def lines(self, ids) -> str:
        chosen = [self.by_id[i] for i in sorted(set(ids)) if i in self.by_id]
        self.retrieved.update(s.id for s in chosen)
        self.shown_times.update(fmt_time(s.start) for s in chosen)
        return "\n".join(self.line(s) for s in chosen)

    def search(self, query: str, limit: int = 5) -> list[int]:
        """Rank segments by how many query words they contain, then by phrase match."""
        q_norm = normalize(query)
        terms = [w for w in q_norm.split() if w not in STOPWORDS] or q_norm.split()
        if not terms:
            return []
        scored = []
        for seg in self.segments:
            words = self._words[seg.id]
            hits = sum(1 for t in set(terms) if t in words or any(w.startswith(t) for w in words if len(t) > 3))
            if hits:
                bonus = 0.5 if q_norm and q_norm in normalize(seg.text) else 0.0
                scored.append((hits + bonus, -seg.id, seg.id))
        scored.sort(reverse=True)
        return [sid for _, _, sid in scored[:limit]]

    def outline(self) -> str:
        if not self.sections:
            first, last = self.segments[0].id, self.segments[-1].id
            return f"(no outline) transcript lines S{first} to S{last}"
        rows = []
        for n, section in enumerate(self.sections, start=1):
            ids = [i for i in section["segment_ids"] if i in self.by_id]
            if ids:
                start = fmt_time(self.by_id[min(ids)].start)
                self.shown_times.add(start)
                rows.append(f"{n}. {section['title']} - starts {start}, lines S{min(ids)} to S{max(ids)}")
            else:
                rows.append(f"{n}. {section['title']}")
        return "\n".join(rows)


def lecture_tools(index: LectureIndex) -> list:
    """Build the four lecture tools bound to one lecture."""

    @tool
    def search_transcript(query: str, max_results: int = 5) -> str:
        """Search the lecture transcript for the lines that talk about a topic.

        Returns matching lines with their line number (S12) and time (03:14),
        plus the line before and after each match for context.

        Args:
            query: a few key words to look for, for example "stomata carbon dioxide"
            max_results: how many matching lines to return, 1 to 10
        """
        hits = index.search(query, max(1, min(10, max_results)))
        if not hits:
            return f'No line in the lecture mentions "{query}". Try other words, or the lecture may not cover it.'
        with_context = {i + d for i in hits for d in (-1, 0, 1)}
        return index.lines(with_context)

    @tool
    def read_transcript(start_line: int, end_line: int) -> str:
        """Read a continuous part of the transcript, from one line number to another.

        Args:
            start_line: first line number to read (the number after S)
            end_line: last line number to read; at most 40 lines per call
        """
        if end_line < start_line:
            start_line, end_line = end_line, start_line
        end_line = min(end_line, start_line + MAX_READ - 1)
        text = index.lines(range(start_line, end_line + 1))
        return text or f"There are no lines S{start_line} to S{end_line}. The transcript has S1 to S{index.segments[-1].id}."

    @tool
    def list_sections() -> str:
        """List the sections of the lecture in order, with the time and line numbers each one covers."""
        return f"Lecture: {index.title}\n{index.outline()}"

    @tool
    def read_board() -> str:
        """Show what was written on the board or slides, with the time each one appeared."""
        if not index.boards:
            return "Nothing was read from the board or slides in this lecture."
        rows = []
        for board in index.boards:
            index.shown_times.add(fmt_time(board["time"]))
            text = " / ".join(line.strip() for line in board["text"].splitlines() if line.strip())
            extra = f" | formulas: {'; '.join(board['formulas'])}" if board.get("formulas") else ""
            rows.append(f"[Board {fmt_time(board['time'])}] {text}{extra}")
        return "\n".join(rows)

    return [search_transcript, read_transcript, list_sections, read_board]
