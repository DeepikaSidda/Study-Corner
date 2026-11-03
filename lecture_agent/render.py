"""Turn the results into notes.md (for students) and lecture.json (for tools)."""

from __future__ import annotations

import json
from pathlib import Path

from .media import Frame
from .schemas import BoardReading, LectureNotes, QuizQuestion
from .transcript import Segment, fmt_time

LETTERS = "ABCDEFGH"


def _at(segment_ids: list[int], by_id: dict[int, Segment]) -> str:
    """'▶ 03:14' for the earliest cited segment, or '' if none."""
    starts = [by_id[i].start for i in segment_ids if i in by_id]
    return f"▶ {fmt_time(min(starts))}" if starts else ""


def _answer_label(q: QuizQuestion) -> str:
    if q.kind == "multiple_choice":
        for letter, option in zip(LETTERS, q.options):
            if option.strip().lower() == q.answer.strip().lower():
                return f"{letter}. {option}"
    return q.answer


def render_markdown(
    notes: LectureNotes,
    quiz: list[QuizQuestion],
    boards: list[tuple[Frame, BoardReading]],
    segments: list[Segment],
    metrics: dict,
) -> str:
    by_id = {s.id: s for s in segments}
    out: list[str] = [f"# {notes.title}", ""]
    out.append(
        f"*Lecture length {fmt_time(metrics['lecture_seconds'])} · "
        f"notes generated in {fmt_time(metrics['processing_seconds'])} · "
        f"▶ times show where to rewatch*"
    )

    out += ["", "## Summary", "", notes.summary.strip()]

    out += ["", "## Key points", ""]
    for kp in notes.key_points:
        out.append(f"- {kp.point} {_at(kp.segment_ids, by_id)}".rstrip())

    out += ["", "## Full notes"]
    for number, section in enumerate(notes.sections, start=1):
        out += ["", f"### {number}. {section.title} {_at(section.segment_ids, by_id)}".rstrip(), ""]
        out += [f"- {b}" for b in section.bullets]

    if notes.key_terms:
        out += ["", "## Key terms", "", "| Term | Meaning | Where |", "|---|---|---|"]
        for term in notes.key_terms:
            meaning = term.definition.replace("|", "/")
            out.append(f"| **{term.term}** | {meaning} | {_at(term.segment_ids, by_id)} |")

    if notes.worked_examples:
        out += ["", "## Worked examples"]
        for ex in notes.worked_examples:
            out += ["", f"### {ex.title} {_at(ex.segment_ids, by_id)}".rstrip(), ""]
            out += [f"{i}. {step}" for i, step in enumerate(ex.steps, start=1)]

    written = [(f, r) for f, r in boards if r.has_content]
    if written:
        out += ["", "## On the board", ""]
        for frame, reading in written:
            out.append(f"**▶ {fmt_time(frame.time)}**")
            if reading.text.strip():
                out += [f"> {line}" for line in reading.text.strip().splitlines() if line.strip()]
            for formula in reading.formulas:
                if formula.strip() not in reading.text:  # already shown in the text
                    out.append(f"> `{formula}`")
            if reading.diagram:
                out.append(f"> *Diagram:* {reading.diagram}")
            out.append("")

    out += ["", "## Quiz", ""]
    for number, q in enumerate(quiz, start=1):
        out.append(f"**{number}.** {q.question}")
        if q.kind == "multiple_choice":
            out += [f"   {letter}. {option}" for letter, option in zip(LETTERS, q.options)]
        else:
            out.append("   *(short answer)*")
        out.append("")

    out += ["## Answer key", ""]
    for number, q in enumerate(quiz, start=1):
        rewatch = _at(q.segment_ids, by_id).replace("▶", "rewatch from")
        out.append(f"**{number}.** {_answer_label(q)} — {q.explanation} *({rewatch})*")
        out.append(f"   > Teacher said: \"{q.evidence_quote}\"")
        out.append("")

    out += ["## Quality report", "", "| Check | Result |", "|---|---|"]
    q = metrics["quiz"]
    n = metrics["notes"]
    rows = [
        ("Transcript segments", n["segments_total"]),
        ("Segments covered by the notes", f"{n['segments_cited']} ({n['coverage']:.0%})"),
        ("Board frames sampled → kept after change detection", f"{metrics['frames_sampled']} → {metrics['frames_kept']}"),
        ("Questions submitted to check_question by the quiz agent", q["checks"]),
        ("Failed the code check (quote not in lecture, bad options)", q["code_failures"]),
        ("Failed the judge agent", q["judge_failures"]),
        ("Rejected as duplicates", q["duplicates"]),
        ("Near-match quotes replaced with the teacher's exact words", q["quotes_replaced_with_transcript"]),
        ("Times steering stopped the agent finishing early", q["completion_nudges"]),
        ("Tool calls", ", ".join(f"{k} {v}" for k, v in q["tool_calls"].items()) or "none"),
        ("Questions in this quiz, all verified", f"{len(quiz)} of {q['target']}"),
    ]
    out += [f"| {label} | {value} |" for label, value in rows]
    out.append("")
    return "\n".join(out)


def write_outputs(
    out_dir: Path,
    notes: LectureNotes,
    quiz: list[QuizQuestion],
    boards: list[tuple[Frame, BoardReading]],
    segments: list[Segment],
    metrics: dict,
) -> tuple[Path, Path]:
    md_path = out_dir / "notes.md"
    json_path = out_dir / "lecture.json"
    md_path.write_text(render_markdown(notes, quiz, boards, segments, metrics), encoding="utf-8")
    payload = {
        "notes": notes.model_dump(),
        "quiz": [q.model_dump() for q in quiz],
        "board": [{"time": f.time, "frame": f.path.name, **r.model_dump()} for f, r in boards],
        "segments": [{"id": s.id, "start": s.start, "end": s.end, "text": s.text} for s in segments],
        "metrics": metrics,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return md_path, json_path


def render_document_html(data: dict) -> str:
    """The notes as one readable HTML document, for the "Show notes" page.

    Built on the server with every model-written string escaped, so it works
    without JavaScript and prints cleanly.
    """
    from html import escape as e

    notes = data["notes"]
    starts = {s["id"]: s["start"] for s in data["segments"]}

    def at(ids) -> str:
        found = [starts[i] for i in ids or [] if i in starts]
        return f' <span class="t">{fmt_time(min(found))}</span>' if found else ""

    out = [f'<h1>{e(notes["title"])}</h1>']
    meta = []
    if data.get("metrics", {}).get("lecture_seconds"):
        meta.append(f"Lecture length {fmt_time(data['metrics']['lecture_seconds'])}")
    meta += [f"{len(notes['sections'])} sections", f"{len(data['quiz'])} quiz questions"]
    out.append(f'<p class="doc-meta">{" · ".join(meta)} · times show where to rewatch</p>')
    if data.get("credit"):
        out.append(f'<p class="doc-meta">{e(data["credit"])}</p>')

    out.append(f'<div class="doc-summary" id="summary"><b>In short</b><p>{e(notes["summary"])}</p></div>')

    out.append('<h2 id="key-points">Key points</h2><ol>')
    out += [f"<li>{e(k['point'])}{at(k['segment_ids'])}</li>" for k in notes["key_points"]]
    out.append("</ol>")

    out.append('<h2 id="full-notes">Full notes</h2>')
    for n, section in enumerate(notes["sections"], start=1):
        out.append(f'<h3 id="s-{n}">{n}. {e(section["title"])}{at(section["segment_ids"])}</h3><ul>')
        out += [f"<li>{e(b)}</li>" for b in section["bullets"]]
        out.append("</ul>")

    if notes.get("key_terms"):
        out.append('<h2 id="key-terms">Key terms</h2><dl>')
        for term in notes["key_terms"]:
            out.append(f"<dt>{e(term['term'])}{at(term['segment_ids'])}</dt><dd>{e(term['definition'])}</dd>")
        out.append("</dl>")

    if notes.get("worked_examples"):
        out.append('<h2 id="worked-examples">Worked examples</h2>')
        for n, ex in enumerate(notes["worked_examples"], start=1):
            out.append(f'<h3 id="ex-{n}">{e(ex["title"])}{at(ex["segment_ids"])}</h3><ol>')
            out += [f"<li>{e(step)}</li>" for step in ex["steps"]]
            out.append("</ol>")

    boards = [b for b in data.get("board", []) if b.get("has_content")]
    if boards:
        out.append('<h2 id="board">On the board</h2>')
        for b in boards:
            lines = "<br>".join(e(line.strip()) for line in b["text"].splitlines() if line.strip())
            extra = "".join(f"<br><code>{e(f)}</code>" for f in b.get("formulas", []) if f.strip() not in b["text"])
            diagram = f'<br><i>Diagram: {e(b["diagram"])}</i>' if b.get("diagram") else ""
            out.append(f'<p class="board-text"><span class="t">{fmt_time(b["time"])}</span><br>{lines}{extra}{diagram}</p>')

    if data["quiz"]:
        out.append('<h2 class="page-break" id="quiz">Quiz</h2>')
        for n, q in enumerate(data["quiz"], start=1):
            out.append(f"<h3>{n}. {e(q['question'])}</h3>")
            if q["kind"] == "multiple_choice":
                out.append('<ol type="A">' + "".join(f"<li>{e(o)}</li>" for o in q["options"]) + "</ol>")
            else:
                out.append('<p class="blank">Answer: ________________________</p>')
        out.append('<h2 id="answer-key">Answer key</h2><ol>')
        for q in data["quiz"]:
            out.append(
                f"<li><b>{e(_answer_label(QuizQuestion(**q)))}</b> — {e(q['explanation'])}"
                f'<br><span class="quote">The teacher said: “{e(q["evidence_quote"])}”</span>{at(q["segment_ids"])}</li>'
            )
        out.append("</ol>")
    return "\n".join(out)
