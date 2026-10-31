"""Single-task agents: board reader, note writer and judge.

Each is a fresh Strands Agent with one narrow job and a structured output
schema. The quiz writer (quiz_agent.py) and the study chat (chat.py) are
tool-using agents; the judge here is called as a tool by the quiz writer.
"""

from __future__ import annotations

from strands import Agent

from . import config
from .media import Frame
from .schemas import BoardReading, JudgeVerdict, LectureNotes, QuizQuestion
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


# --------------------------------------------------------------- judge
# The quiz writer itself is a tool-using agent in quiz_agent.py; this judge is
# called from inside its check_question tool (an agent used as a tool).
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
