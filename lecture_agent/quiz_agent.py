"""The quiz agent: a Strands tool loop that writes, checks and fixes questions.

The agent reads the lecture through tools, writes one question at a time and
submits it with `check_question`. That tool runs the code checks and then a
second agent, the judge (an agent used as a tool). Only questions that pass
both are saved. The agent sees the reasons for every failure, so it fixes the
question or picks a different fact.

Around the loop:
  - QuizCompletion steering stops the agent finishing before the quiz is full
  - BudgetHook caps calls to each tool and to the model
  - Limits caps the number of turns and tokens for the whole run
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError
from strands import Agent, tool

from . import agents, config
from .checker import code_check, evidence_lines, is_duplicate, normalize, teacher_words
from .guards import BudgetHook, QuizCompletion, TraceHook
from .schemas import LectureNotes, QuizQuestion
from .tools import LectureIndex, lecture_tools
from .transcript import Segment

QUIZ_AGENT_PROMPT = """You build a quiz that checks whether a student understood a recorded lecture.

How to work:
1. Look at the outline. Spread the questions across ALL sections, not only the first.
2. For each question, read the relevant lines with read_transcript or search_transcript.
3. Write ONE question and submit it with check_question.
4. If check_question says FAILED, read the reasons. Fix the question, or choose a different
   fact, and submit again. Never resubmit an unchanged question.
5. Continue until check_question says the quiz is complete, then reply DONE.
   If the lecture has no facts left that are not already covered, reply DONE (NOT ENOUGH FACTS)
   rather than asking about the same fact again.

Rules for every question:
- Answerable from the lecture alone. No outside knowledge.
- evidence_quote: 8 to 30 words copied EXACTLY from one or two transcript lines. Copy, do not paraphrase.
- segment_ids: the line numbers (after S) the quote comes from.
- multiple_choice: exactly 4 options, one correct, `answer` identical to that option, wrong options
  plausible but clearly wrong according to the lecture.
- short_answer: no options, a short answer phrase.
- About two thirds multiple_choice. Mix recall and understanding. Never ask about the same fact twice.
- Write question, options, answer and explanation in {language}."""


@dataclass
class QuizBook:
    """Holds the verified questions and counts every check outcome."""

    segments: list[Segment]
    target: int
    judge: object = None
    accepted: list[QuizQuestion] = field(default_factory=list)
    checks: int = 0
    code_failures: int = 0
    judge_failures: int = 0
    duplicates: int = 0
    quotes_replaced: int = 0
    failures: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.by_id = {s.id: s for s in self.segments}

    @property
    def complete(self) -> bool:
        return len(self.accepted) >= self.target

    def check(self, question: QuizQuestion) -> str:
        self.checks += 1
        if self.complete:
            return "The quiz is already complete. Do not submit more questions. Reply DONE."

        problems, _score = code_check(question, self.by_id)
        if problems:
            self.code_failures += 1
        elif is_duplicate(question, self.accepted):
            self.duplicates += 1
            problems = [
                "it tests the same fact as a question already saved (same wording or same evidence). "
                "Pick a fact from a part of the lecture you have not used yet"
            ]
        elif self.judge is not None:
            verdict = self.judge(question, evidence_lines(question, self.by_id))
            if not verdict.supported:
                self.judge_failures += 1
                problems = [f"the fact checker says: {verdict.reason}"]

        if problems:
            self.failures.append({"question": question.question, "problems": problems})
            return "FAILED. Problems: " + "; ".join(problems)

        # A near-match quote passes, but the answer key promises the teacher's
        # own words, so store the matching transcript text instead.
        text = " ".join(s.text for s in evidence_lines(question, self.by_id))
        if normalize(question.evidence_quote) not in normalize(text):
            question.evidence_quote = teacher_words(question, self.by_id)
            self.quotes_replaced += 1
        self.accepted.append(question)
        done = " The quiz is complete. Reply DONE." if self.complete else ""
        return f"PASSED and saved ({len(self.accepted)} of {self.target}).{done}"


def build_quiz(
    segments: list[Segment],
    notes: LectureNotes,
    target: int,
    language: str,
    use_judge: bool = True,
    log=print,
) -> tuple[list[QuizQuestion], dict]:
    index = LectureIndex(
        segments=segments,
        title=notes.title,
        sections=[{"title": s.title, "segment_ids": s.segment_ids} for s in notes.sections],
    )
    book = QuizBook(segments=segments, target=target, judge=agents.judge_question if use_judge else None)

    @tool
    def check_question(
        question: str,
        kind: str,
        answer: str,
        explanation: str,
        evidence_quote: str,
        segment_ids: list[int],
        options: list[str] | None = None,
    ) -> str:
        """Check one quiz question against the lecture. If it passes every check it is saved to the quiz.

        Returns PASSED with the number saved so far, or FAILED with the reasons to fix.

        Args:
            question: the question text
            kind: "multiple_choice" or "short_answer"
            answer: the correct answer; for multiple_choice it must be identical to one of the options
            explanation: one or two sentences explaining why the answer is correct
            evidence_quote: 8 to 30 words copied exactly from the transcript that prove the answer
            segment_ids: line numbers (the number after S) that the evidence quote comes from
            options: exactly 4 options for multiple_choice; leave empty for short_answer
        """
        try:
            q = QuizQuestion(
                question=question, kind=kind, options=options or [], answer=answer,
                explanation=explanation, evidence_quote=evidence_quote, segment_ids=segment_ids,
            )
        except ValidationError as exc:
            return f"FAILED. Problems: invalid field {exc.errors()[0]['loc']}: {exc.errors()[0]['msg']}"
        outcome = book.check(q)
        log(f"    check_question: {outcome[:90]}  <- {question[:60]}")
        return outcome

    search, read, outline, _board = lecture_tools(index)
    budget = BudgetHook(
        tool_limits={"check_question": target * 3 + 4, "read_transcript": 60, "search_transcript": 40},
        max_model_calls=target * 8 + 20,
    )
    trace = TraceHook()
    steering = QuizCompletion(book, max_nudges=3)
    agent = Agent(
        model=config.model(temperature=0.4),
        system_prompt=QUIZ_AGENT_PROMPT.format(language=language),
        tools=[outline, read, search, check_question],
        hooks=[budget, trace],
        plugins=[steering],
        callback_handler=None,
    )

    prompt = (
        f"Build a quiz of {target} questions.\n\n"
        f"LECTURE: {notes.title}\n"
        f"OUTLINE:\n{index.outline()}\n\n"
        "Start by reading the first section."
    )
    stop_reason = "error"
    usage = {}
    try:
        result = agent(prompt, limits={"turns": target * 6 + 12, "total_tokens": 800_000})
        stop_reason = result.stop_reason
        usage = dict(result.metrics.accumulated_usage)
    except Exception as exc:  # noqa: BLE001 - keep whatever was verified so far
        log(f"  quiz agent stopped early: {type(exc).__name__}: {str(exc)[:160]}")

    tool_counts: dict[str, int] = {}
    for call in trace.calls:
        tool_counts[call["tool"]] = tool_counts.get(call["tool"], 0) + 1

    stats = {
        "target": target,
        "accepted": len(book.accepted),
        "checks": book.checks,
        "code_failures": book.code_failures,
        "judge_failures": book.judge_failures,
        "duplicates": book.duplicates,
        "quotes_replaced_with_transcript": book.quotes_replaced,
        "completion_nudges": steering.nudges,
        "budget_blocks": list(budget.blocked),
        "tool_calls": tool_counts,
        "stop_reason": stop_reason,
        "tokens": usage.get("totalTokens"),
        "rejected": book.failures,
    }
    return spread_answers(book.accepted), stats


def spread_answers(questions: list[QuizQuestion]) -> list[QuizQuestion]:
    """Reorder multiple-choice options so the right answer is not always A.

    Models nearly always write the correct option first. The answer moves to a
    rotating position (A, B, C, D, ...) from a seeded start, and the wrong options
    are shuffled, so the same quiz always comes out in the same order.
    """
    import hashlib
    import random

    seed = hashlib.sha256("|".join(q.question for q in questions).encode("utf-8")).digest()
    rng = random.Random(seed)
    slot = rng.randrange(4)
    out = []
    for q in questions:
        if q.kind != "multiple_choice" or q.answer not in q.options:
            out.append(q)
            continue
        wrong = [o for o in q.options if o != q.answer]
        rng.shuffle(wrong)
        position = slot % len(q.options)
        wrong.insert(position, q.answer)
        slot += 1 + (rng.random() < 0.3)  # mostly rotate, sometimes skip a letter
        out.append(q.model_copy(update={"options": wrong}))
    return out
