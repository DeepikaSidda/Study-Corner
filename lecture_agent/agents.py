"""The four agents: board reader, note writer, quiz writer and judge.

Each agent is a fresh Strands Agent with one narrow job and a structured
output schema. The order they run in is plain Python in pipeline.py, not a
model decision: the steps are known in advance, so code guarantees them.
"""

from __future__ import annotations

from strands import Agent

from . import config
from .media import Frame
from .schemas import BoardReading, JudgeVerdict, LectureNotes, QuizQuestion, QuizSet
from .transcript import Segment, fmt_time, format_for_prompt


def _ask(system_prompt: str, prompt, schema, temperature: float = 0.2):
    agent = Agent(model=config.model(temperature), system_prompt=system_prompt, callback_handler=None)
    result = agent(prompt, structured_output_model=schema)
    output = result.structured_output
    if output is None:
        raise RuntimeError(f"{schema.__name__}: the model returned no structured output")
    return output


# --------------------------------------------------------------- board reader
BOARD_PROMPT = """You read one frame from a recorded classroom lecture.
Copy any text written on the board, whiteboard or slide exactly as written.
List every formula or equation separately in `formulas`.
If a diagram is shown, describe it in one sentence in `diagram`.
If a word cannot be read, write [unclear] instead of guessing.
If the frame shows no written content (for example only the teacher), set
has_content to false and leave the other fields empty."""


def read_board(frame: Frame) -> BoardReading:
    image = frame.path.read_bytes()
    prompt = [
        {"text": f"Frame at {fmt_time(frame.time)}. Read the board or slide."},
        {"image": {"format": "jpeg", "source": {"bytes": image}}},
    ]
    return _ask(BOARD_PROMPT, prompt, BoardReading, temperature=0.0)


def format_boards(boards: list[tuple[Frame, BoardReading]]) -> str:
    lines = []
    for frame, reading in boards:
        if not reading.has_content:
            continue
        parts = [reading.text.strip().replace("\n", " / ")]
        if reading.formulas:
            parts.append("formulas: " + "; ".join(reading.formulas))
        if reading.diagram:
            parts.append("diagram: " + reading.diagram)
        lines.append(f"[Board {fmt_time(frame.time)}] " + " | ".join(p for p in parts if p))
    return "\n".join(lines) or "(no board or slide content)"


# --------------------------------------------------------------- note writer
NOTES_PROMPT = """You turn a recorded lecture into study notes for a student who missed the class.

Rules:
- Use ONLY what the teacher said in the transcript or wrote on the board.
  Never add facts, examples or explanations from outside the lecture.
- Every key point, section, key term and worked example must list the
  transcript segment numbers it comes from (the numbers after S, e.g. [S12 ...] -> 12).
- Write in {language}, in simple words a 14-year-old can follow. Keep
  technical terms, names, numbers and formulas exactly as given.
- `sections` are the full notes. Follow the order of the lecture and cover
  all of it, not only the beginning.
- `summary` is 4 to 6 sentences covering the whole lecture.
- 5 to 10 key points. Include key terms and worked examples only if the
  teacher actually defined terms or worked through an example."""


def write_notes(segments: list[Segment], board_text: str, language: str) -> LectureNotes:
    prompt = (
        "TRANSCRIPT (each line starts with its segment number and time):\n"
        f"{format_for_prompt(segments)}\n\n"
        "BOARD AND SLIDES (read from video frames):\n"
        f"{board_text}"
    )
    return _ask(NOTES_PROMPT.format(language=language), prompt, LectureNotes)


# --------------------------------------------------------------- quiz writer
QUIZ_PROMPT = """You write quiz questions that check whether a student understood a lecture.

Rules:
- Every question must be answerable from the transcript alone. Never use
  outside knowledge.
- evidence_quote: copy 8 to 30 words EXACTLY from the transcript that prove
  the answer. Copy the words; do not paraphrase or translate them.
- segment_ids: the segment numbers the evidence quote comes from.
- multiple_choice: exactly 4 options, one correct. `answer` must be
  identical to one option. Wrong options must be plausible but clearly wrong
  according to the lecture.
- short_answer: `options` is empty and `answer` is a short phrase.
- Spread the questions across the whole lecture. Mix recall questions with
  questions that check understanding. Do not ask two questions about the same fact.
- Write question, options, answer and explanation in {language}."""


def write_quiz(segments: list[Segment], notes: LectureNotes, count: int, language: str) -> list[QuizQuestion]:
    prompt = (
        f"Write {count} questions: about two thirds multiple_choice, the rest short_answer.\n\n"
        f"LECTURE TITLE: {notes.title}\n\n"
        "TRANSCRIPT:\n"
        f"{format_for_prompt(segments)}"
    )
    return _ask(QUIZ_PROMPT.format(language=language), prompt, QuizSet, temperature=0.4).questions


def rewrite_questions(
    segments: list[Segment],
    failed: list[tuple[QuizQuestion, list[str]]],
    keep: list[QuizQuestion],
    language: str,
) -> list[QuizQuestion]:
    """Ask for replacements for questions that failed checking, with the reasons."""
    problems = "\n\n".join(
        f"REJECTED QUESTION: {q.question}\nPROBLEMS: " + "; ".join(reasons) for q, reasons in failed
    )
    already = "\n".join(f"- {q.question}" for q in keep) or "(none)"
    prompt = (
        f"These questions failed checking. Write {len(failed)} replacement questions "
        "that follow every rule. Fix the listed problems; a replacement may cover a "
        "different part of the lecture.\n\n"
        f"{problems}\n\n"
        f"DO NOT REPEAT THESE ACCEPTED QUESTIONS:\n{already}\n\n"
        "TRANSCRIPT:\n"
        f"{format_for_prompt(segments)}"
    )
    return _ask(QUIZ_PROMPT.format(language=language), prompt, QuizSet, temperature=0.4).questions


# --------------------------------------------------------------- judge
JUDGE_PROMPT = """You are a strict fact checker for quiz questions about a lecture.
You get a few lines from the lecture transcript and one quiz question with its answer.
Set supported to true only if those lines alone prove the given answer is correct,
and, for multiple choice, the lines do not also support a different option.
The question may be written in a different language from the lines; judge the meaning."""


def judge_question(question: QuizQuestion, lines: list[Segment]) -> JudgeVerdict:
    options = "\n".join(f"- {o}" for o in question.options) or "(short answer)"
    prompt = (
        f"LECTURE LINES:\n{format_for_prompt(lines)}\n\n"
        f"QUESTION: {question.question}\n"
        f"OPTIONS:\n{options}\n"
        f"GIVEN ANSWER: {question.answer}"
    )
    return _ask(JUDGE_PROMPT, prompt, JudgeVerdict, temperature=0.0)
