"""Grounding checks: make sure notes and quiz come from the lecture.

Two layers, cheapest first:
  1. Code checks. The cited segments must exist, the evidence quote must
     really appear in them, and a multiple-choice answer must be one of the
     options. A model cannot argue its way past these.
  2. An LLM judge, run only on questions that pass the code checks, decides
     whether the cited lines actually prove the answer.

Questions that fail are sent back for rewriting with the reasons. After the
last round, anything still failing is dropped, so every question that ships
has passed both layers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable

from .schemas import JudgeVerdict, LectureNotes, QuizQuestion
from .transcript import Segment

MIN_QUOTE_WORDS = 4
MIN_QUOTE_SCORE = 0.85
DUPLICATE_RATIO = 0.9


def normalize(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def quote_score(quote: str, text: str) -> float:
    """1.0 if the quote appears word for word, otherwise the best fuzzy match.

    Fuzzy matching tolerates small slips (a dropped "the", different
    punctuation) but not a paraphrase.
    """
    q, t = normalize(quote), normalize(text)
    if not q or not t:
        return 0.0
    if q in t:
        return 1.0
    q_words, t_words = q.split(), t.split()
    size = len(q_words)
    best = 0.0
    for i in range(max(1, len(t_words) - size + 1)):
        window = " ".join(t_words[i : i + size])
        best = max(best, SequenceMatcher(None, q, window).ratio())
    return best


def evidence_lines(question: QuizQuestion, by_id: dict[int, Segment]) -> list[Segment]:
    """The cited segments plus their immediate neighbours.

    A sentence the teacher said can be split across two segments, so the
    neighbours are searched too. Timestamps still come from the cited ids.
    """
    wanted: set[int] = set()
    for sid in question.segment_ids:
        if sid in by_id:
            wanted.update({sid - 1, sid, sid + 1})
    return [by_id[i] for i in sorted(wanted) if i in by_id]


def code_check(question: QuizQuestion, by_id: dict[int, Segment]) -> tuple[list[str], float]:
    problems: list[str] = []

    if not question.segment_ids:
        problems.append("no segment_ids given")
    unknown = [sid for sid in question.segment_ids if sid not in by_id]
    if unknown:
        problems.append(f"segment_ids {unknown} do not exist")

    score = 0.0
    if len(normalize(question.evidence_quote).split()) < MIN_QUOTE_WORDS:
        problems.append("evidence_quote is too short to prove anything")
    else:
        text = " ".join(s.text for s in evidence_lines(question, by_id))
        score = quote_score(question.evidence_quote, text)
        if score < MIN_QUOTE_SCORE:
            problems.append(
                f"evidence_quote was not found in segments {question.segment_ids} "
                f"(match {score:.2f}); copy the words exactly"
            )

    if question.kind == "multiple_choice":
        options = [normalize(o) for o in question.options]
        if not 3 <= len(options) <= 5:
            problems.append(f"multiple_choice needs 4 options, got {len(options)}")
        if len(set(options)) != len(options):
            problems.append("options are not all different")
        if options.count(normalize(question.answer)) != 1:
            problems.append("answer is not exactly one of the options")
    elif not question.answer.strip():
        problems.append("short_answer has an empty answer")

    return problems, score


@dataclass
class QuizReport:
    accepted: list[QuizQuestion] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    generated: int = 0
    first_pass_ok: int = 0
    rewrite_rounds: int = 0
    code_failures: int = 0
    judge_failures: int = 0
    duplicates: int = 0
    quotes_replaced_with_transcript: int = 0


def teacher_words(question: QuizQuestion, by_id: dict[int, Segment]) -> str:
    """The transcript text that best matches the model's evidence quote.

    A near-match quote passes the check, but it is not what the teacher said:
    in one real run the model changed "It traps the energy of sunlight" to
    "Chlorophyll traps the energy of sunlight". The answer key promises the
    teacher's own words, so it shows this text instead.
    """
    # Real Transcribe segments often hold several sentences, so compare whole
    # sentences (and pairs, for quotes that run across a full stop) rather
    # than arbitrary word windows, which can grab a look-alike word.
    text = " ".join(s.text for s in evidence_lines(question, by_id))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", text) if s.strip()]
    candidates = sentences + [f"{a} {b}" for a, b in zip(sentences, sentences[1:])]
    if not candidates:
        return question.evidence_quote
    quote = normalize(question.evidence_quote)

    def score(candidate: str) -> tuple[float, int]:
        return (round(SequenceMatcher(None, quote, normalize(candidate)).ratio(), 3), -len(candidate))

    return max(candidates, key=score)


def _is_duplicate(question: QuizQuestion, accepted: list[QuizQuestion]) -> bool:
    q = normalize(question.question)
    return any(SequenceMatcher(None, q, normalize(a.question)).ratio() >= DUPLICATE_RATIO for a in accepted)


def verify_quiz(
    questions: list[QuizQuestion],
    segments: list[Segment],
    judge: Callable[[QuizQuestion, list[Segment]], JudgeVerdict] | None,
    rewrite: Callable[[list[tuple[QuizQuestion, list[str]]], list[QuizQuestion]], list[QuizQuestion]] | None,
    max_rounds: int = 2,
    log=print,
) -> QuizReport:
    by_id = {s.id: s for s in segments}
    report = QuizReport(generated=len(questions))
    pending = list(questions)
    round_no = 0

    while pending:
        failed: list[tuple[QuizQuestion, list[str]]] = []
        for question in pending:
            # Cheapest checks first; the judge is a model call, so it runs last.
            problems, _score = code_check(question, by_id)
            if problems:
                report.code_failures += 1
            elif _is_duplicate(question, report.accepted):
                report.duplicates += 1
                problems = ["duplicate of an accepted question"]
            elif judge is not None:
                verdict = judge(question, evidence_lines(question, by_id))
                if not verdict.supported:
                    report.judge_failures += 1
                    problems = [f"judge: {verdict.reason}"]

            if problems:
                failed.append((question, problems))
            else:
                text = " ".join(s.text for s in evidence_lines(question, by_id))
                if normalize(question.evidence_quote) not in normalize(text):
                    question.evidence_quote = teacher_words(question, by_id)
                    report.quotes_replaced_with_transcript += 1
                report.accepted.append(question)
                if round_no == 0:
                    report.first_pass_ok += 1

        log(f"  round {round_no}: {len(pending) - len(failed)} passed, {len(failed)} failed")
        if not failed or rewrite is None or round_no >= max_rounds:
            report.rejected = [{"question": q.question, "problems": p} for q, p in failed]
            break
        round_no += 1
        report.rewrite_rounds = round_no
        pending = rewrite(failed, report.accepted)
        report.generated += len(pending)

    return report


def clean_notes(notes: LectureNotes, segments: list[Segment]) -> dict:
    """Remove citations to segments that do not exist, and measure coverage.

    Coverage is the share of transcript segments that the notes cite. A low
    number means part of the lecture was left out.
    """
    valid = {s.id for s in segments}
    invalid = 0
    cited: set[int] = set()

    def clean(item) -> bool:
        nonlocal invalid
        good = [sid for sid in item.segment_ids if sid in valid]
        invalid += len(item.segment_ids) - len(good)
        item.segment_ids = good
        cited.update(good)
        return bool(good)

    # A key point, term or example that cannot be traced to the lecture is
    # removed: it may be something the model added. Sections are the full
    # notes, so they are kept, but counted if unsourced.
    removed = 0
    for name in ("key_points", "key_terms", "worked_examples"):
        items = getattr(notes, name)
        kept = [item for item in items if clean(item)]
        removed += len(items) - len(kept)
        setattr(notes, name, kept)
    unsourced_sections = sum(1 for section in notes.sections if not clean(section))

    return {
        "invalid_citations_removed": invalid,
        "unsourced_items_removed": removed,
        "sections_without_source": unsourced_sections,
        "segments_cited": len(cited),
        "segments_total": len(valid),
        "coverage": round(len(cited) / len(valid), 3) if valid else 0.0,
    }
