"""Grounding checks: make sure notes and quiz come from the lecture.

Used by the quiz agent's check_question tool (quiz_agent.py), cheapest first:
  1. Code checks. The cited segments must exist, the evidence quote must
     really appear in them, and a multiple-choice answer must be one of the
     options. A model cannot argue its way past these.
  2. Only then, the judge agent decides whether the cited lines prove the answer.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .schemas import LectureNotes, QuizQuestion
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


def is_duplicate(question: QuizQuestion, accepted: list[QuizQuestion]) -> bool:
    """Same question, or the same fact asked a different way.

    Comparing wording alone is not enough: in a real run the quiz agent asked
    "What are the three things plants need?" and later "Which is NOT a
    requirement?", both proven by the identical quote. Two questions whose
    evidence quotes match, or where one quote contains the other, test the
    same fact.
    """
    q = normalize(question.question)
    quote = normalize(question.evidence_quote)
    for other in accepted:
        if SequenceMatcher(None, q, normalize(other.question)).ratio() >= DUPLICATE_RATIO:
            return True
        other_quote = normalize(other.evidence_quote)
        if quote and other_quote and (quote in other_quote or other_quote in quote):
            return True
    return False


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
