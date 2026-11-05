"""Single-task agents: board reader, note writer and judge.

Each is a fresh Strands Agent with one narrow job and a structured output
schema. The quiz writer (quiz_agent.py) and the study chat (chat.py) are
tool-using agents; the judge here is called as a tool by the quiz writer.
"""

from __future__ import annotations

import time

from strands import Agent

from . import config
from .media import Frame
from .schemas import BoardReading, JudgeVerdict, LectureNotes, QuizQuestion
from .transcript import Segment, fmt_time, format_for_prompt


# Bedrock errors that go away when the same request is sent again, e.g. Nova
# now and then streaming a broken tool-use reply ("Model produced invalid
# sequence as part of ToolUse").
TRANSIENT_ERRORS = ("modelStreamErrorException", "invalid sequence", "ThrottlingException",
                    "ServiceUnavailable", "InternalServerException", "ModelThrottled")
RETRIES = 2


def is_transient(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return any(marker in text for marker in TRANSIENT_ERRORS)


def _ask(system_prompt: str, prompt, schema, temperature: float = 0.2, retries: int = RETRIES):
    for attempt in range(retries + 1):
        # A fresh agent each try, so a broken reply never stays in the history.
        agent = Agent(model=config.model(temperature), system_prompt=system_prompt, callback_handler=None)
        try:
            result = agent(prompt, structured_output_model=schema)
            break
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is a known transient error
            if attempt == retries or not is_transient(exc):
                raise
            time.sleep(2 * (attempt + 1))
    output = result.structured_output
    if output is None:
        raise RuntimeError(f"{schema.__name__}: the model returned no structured output")
    return output


# --------------------------------------------------------------- board reader
BOARD_PROMPT = """You read one frame from a recorded lecture and write down only what a student
needs to study from it. Copy what is shown; never add or invent anything.

- text: the slide, board or whiteboard content: title, headings and points, as
  written, one line per line.
- code: the program code being taught, copied exactly with its indentation.
  Leave out line numbers, and boilerplate the lesson isn't about, such as
  "# -*- coding: utf-8 -*-", "@author", "Created on", empty docstrings.
- output: only the meaningful result when code is run: the printed values,
  or the final error line (for example "ZeroDivisionError: division by zero").
- formulas: each formula or equation, copied exactly.
- diagram: one sentence if a diagram is shown.

Never copy: stack traces, file paths, "File ... line ..." lines, console
prompts like "In [2]:" or "runfile(...)", menus, toolbars, tabs, window
titles, the taskbar, or the webcam picture of the teacher.
If a word cannot be read, write [unclear] instead of guessing.
If the frame has no teaching content (only the teacher, a desktop or an empty
editor), set has_content to false and leave the other fields empty."""


import re as _re

_NOISE = _re.compile(
    r"""^\s*(?:
        File\s+["'].*["'],\s*line\s+\d+       # traceback frame
      | Traceback\s+\(most\s+recent\s+call     # traceback header
      | In\s*\[\d*\]:?                         # IPython prompt
      | Out\s*\[\d*\]:?
      | runfile\(|debugfile\(                  # Spyder run commands
      | exec\(compile\(|execfile\(
      | [A-Za-z]:[\\/]\S*                      # Windows path
      | (?:/[\w.\-]+){2,}/?\s*$                # Unix path on its own
      | \#\s*-\*-\s*coding                     # encoding line
      | @author\b|Created\s+on\b
      | \.\.\.\s*$|-{3,}\s*$|\^+\s*$           # ellipsis, rulers, caret markers
    )""", _re.X | _re.I)
_LINE_NUMBER = _re.compile(r"^\s{0,3}\d{1,3}(?:\s|$)")


def _clean_lines(text: str, *, code: bool = False) -> str:
    lines = (text or "").replace("\r", "").split("\n")
    if code:
        numbered = [l for l in lines if l.strip()]
        # Editor line numbers ("1 x = 10", "2 y = 0"): strip them when most lines have one.
        if numbered and sum(bool(_LINE_NUMBER.match(l)) for l in numbered) >= 0.6 * len(numbered):
            lines = [_LINE_NUMBER.sub("", l, count=1) if _LINE_NUMBER.match(l) else l for l in lines]
    kept = [l.rstrip() for l in lines if not _NOISE.match(l)]
    while kept and not kept[0].strip():
        kept.pop(0)
    out, blank = [], False
    for l in kept:
        if not l.strip():
            if not blank and out:
                out.append("")
            blank = True
        else:
            out.append(l)
            blank = False
    while out and not out[-1].strip():
        out.pop()
    if code:
        # Docstrings that only hold editor boilerplate ('"""' ... '"""' with nothing else).
        joined = "\n".join(out)
        joined = _re.sub(r'^\s*("""|\'\'\')\s*\n\s*\1\s*\n?', "", joined, flags=_re.M)
        out = joined.split("\n") if joined else []
    return "\n".join(out).strip("\n")


def tidy_board(reading: BoardReading) -> BoardReading:
    """Safety net after the model: drop screen noise it still copied, keep the lesson."""
    text = _clean_lines(reading.text)
    code = _clean_lines(reading.code, code=True)
    output_lines = [l for l in _clean_lines(reading.output).split("\n") if l.strip()]
    # For an error, the final "SomethingError: message" line is what matters.
    errors = [l for l in output_lines if _re.match(r"^\s*\w+(?:Error|Exception)\b", l)]
    output = "\n".join(errors[-1:] if errors else output_lines[-6:])
    has = reading.has_content and bool(text or code or output or reading.formulas or reading.diagram)
    return reading.model_copy(update={"text": text, "code": code, "output": output, "has_content": has})


def board_key(reading: BoardReading) -> str:
    """What a reading teaches, ignoring spacing: used to drop repeats of the same screen."""
    return _re.sub(r"\s+", " ", f"{reading.text}|{reading.code}|{reading.output}").strip().lower()


BOARD_TO_ENGLISH = """
The lecture is not in English. Write all board text in English: translate
words in other languages, and keep names, code, numbers, URLs and formulas
exactly as written. Copy the main content: titles, headings, slide text, and
any large caption or text placed over the video. Skip menu items and small
labels on a web page."""


def read_board(frame: Frame, to_english: bool = False, language: str = "auto") -> BoardReading:
    """Read one frame. to_english: for non-English lectures, write the board in English.

    Nova Pro failed on every try (modelStreamErrorException) when copying a
    screen full of Hindi into its structured reply; asking for the main
    content in English avoids that and matches the English notes. Only one
    retry: a frame that fails twice is skipped by the pipeline.
    """
    image = frame.path.read_bytes()
    prompt = [
        {"text": f"Frame at {fmt_time(frame.time)}. Read the board or slide."},
        {"image": {"format": "jpeg", "source": {"bytes": image}}},
    ]
    if to_english:
        return _read_board_plain(prompt, language)
    return tidy_board(_ask(BOARD_PROMPT, prompt, BoardReading, temperature=0.0, retries=1))


BOARD_PLAIN_PROMPT = """You read one frame from a recorded lecture that is not in English.
Copy ONLY the parts written in English letters: English words, code, numbers,
web addresses and formulas, exactly as written. Ignore all text in other
scripts (for example Hindi). Do not translate and do not add anything.
Skip menu items and small labels. One line per line of content.
If there is no such text (for example only the teacher, or only Hindi text),
reply with exactly: NONE"""


def _read_board_plain(prompt, source_language: str = "auto") -> BoardReading:
    """Plain-text reply, no structured output: Nova's tool-use reply kept failing
    (modelStreamErrorException) on screens full of Hindi, taking about a minute
    per try. A plain reply has no tool-use format to break."""
    from .guards import strip_thinking

    # A slide holds at most a few hundred words. Nova sometimes repeats itself
    # on these screens until the token limit (about 65 s at 8,000 tokens); a
    # small limit stops that in a few seconds, and what was read so far is kept.
    last = None
    for attempt in range(2):
        agent = Agent(model=config.model(0.0, max_tokens=700), system_prompt=BOARD_PLAIN_PROMPT,
                      callback_handler=None)
        try:
            text = strip_thinking(str(agent(prompt))).strip()
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if "MaxTokens" in type(exc).__name__:
                partial = _partial_text(agent)
                if partial:
                    text = _dedupe_lines(partial)
                    break
                raise
            if not is_transient(exc):
                raise
            time.sleep(2)
    else:
        raise last
    text = _dedupe_lines(text)
    if not text or text.upper().rstrip(".") == "NONE":
        return BoardReading(has_content=False)
    # Nova Pro can't read Hindi script reliably: asked to translate a Hindi
    # slide it invented "What is the capital of Madhya Pradesh?", and copying
    # it gave "Ferry firmware". Amazon Textract and Rekognition don't read
    # Hindi either. So only Latin-script text (English, code, URLs, numbers)
    # is kept; the spoken Hindi still comes in through Transcribe + Translate.
    kept = [line for line in text.splitlines()
            if line.strip() and all(ord(ch) <= 0x2FF for ch in line)
            and sum(ch.isalnum() for ch in line) >= 3]
    if not kept:
        return BoardReading(has_content=False)
    return BoardReading(has_content=True, text="\n".join(kept))


def _partial_text(agent) -> str:
    """Text the model wrote before it hit the token limit (the last assistant message)."""
    from .guards import strip_thinking

    for message in reversed(agent.messages or []):
        if message.get("role") == "assistant":
            return strip_thinking(" ".join(b.get("text", "") for b in message.get("content", []))).strip()
    return ""


def _dedupe_lines(text: str) -> str:
    """Drop repeated lines, which is how the model's loops show up."""
    seen, out = set(), []
    for line in text.splitlines():
        key = line.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(line.rstrip())
    return "\n".join(out[:40])


def format_boards(boards: list[tuple[Frame, BoardReading]]) -> str:
    lines = []
    for frame, reading in boards:
        if not reading.has_content:
            continue
        parts = [reading.text.strip().replace("\n", " / ")]
        if reading.code.strip():
            parts.append("code: " + reading.code.strip().replace("\n", " ⏎ "))
        if reading.output.strip():
            parts.append("output: " + reading.output.strip().replace("\n", " / "))
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
