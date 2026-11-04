"""Ask the lecture: a study chat that answers only from one recorded lecture.

A Strands agent with the four lecture tools. Around it:
  - CitationCheck steering: every [mm:ss] in an answer must be the time of a
    transcript line the agent read in this turn, so it cannot invent a source
  - BudgetHook: at most a few tool and model calls per question
  - FileSessionManager: each student's conversation is saved, so a follow-up
    like "and where does it come from?" works, even after a restart
  - SlidingWindowConversationManager: long chats stay inside the context window
"""

from __future__ import annotations

import re
from pathlib import Path

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.session import FileSessionManager

from . import config
from .guards import (
    ANY_TIME_BRACKET,
    CITED_TIME,
    GUIDANCE_PREFIX,
    BudgetHook,
    CitationCheck,
    TraceHook,
    strip_thinking,
    unverified_code,
)
from .tools import LectureIndex, lecture_tools

STUDENT_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
MAX_QUESTION_CHARS = 1000

CHAT_PROMPT = """You are a study helper for one recorded lecture. A student who missed the class is asking you about it.

Rules:
- Answer ONLY from this lecture. Before answering a question about its content, call
  search_transcript (or read_transcript) to find the lines that cover it.
- After each fact, cite the time of the transcript line it came from in square brackets,
  copied exactly as the tools show it, for example [03:14].
- Cite one time per bracket. Never write ranges like [00:11-00:37].
- If the lecture does not cover the question, say "The lecture does not cover this." To point
  to the closest part, call list_sections and cite that section's start time.
- For what was written on the board, use read_board and cite the board's time.
- Never write code, formulas or numbers of your own. Only show code that read_board returned, copied
  exactly. If the teacher only described the code out loud, explain it in words and cite the time,
  so the student can see the real code in the video.
- Messages that start with [automatic check] come from a fact-checking system, not the
  student. Fix your answer as they say, and do not mention them.
- Keep answers short: 2 to 5 sentences, simple words, in {language}.
- list_sections shows the outline; read_board shows what was written on the board."""


# Optional words just before a cited time that only make sense with the time:
# ", which starts at", " from", " at around". Removed together with the time.
LEAD_IN = (
    r"(?:,?\s+(?:(?:which|that)\s+)?"
    r"(?:(?:starts?|begins?|is|are|was|comes?|appears?|happens?|explained|covered|discussed)\s+)?"
    r"(?:at\s+around|at|from|around|near)(?=\s*\[))?"
)


def _parse_time(label: str) -> float:
    parts = [int(p) for p in label.split(":")]
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return float(seconds)


def _check_student(student_id: str) -> None:
    if not STUDENT_ID.match(student_id or ""):
        raise ValueError("invalid student id")


def _build(job_dir: Path, student_id: str, language: str):
    _check_student(student_id)
    index = LectureIndex.from_lecture_json(job_dir / "lecture.json")
    trace = TraceHook()
    citations = CitationCheck(index, max_revisions=2)
    session = FileSessionManager(session_id=f"chat-{student_id}", storage_dir=str(job_dir / "chat"))
    agent = Agent(
        model=config.model(temperature=0.2),
        system_prompt=CHAT_PROMPT.format(language=language),
        tools=lecture_tools(index),
        hooks=[BudgetHook(default_tool_limit=6, max_model_calls=12), trace],
        plugins=[citations],
        session_manager=session,
        conversation_manager=SlidingWindowConversationManager(window_size=30),
        callback_handler=None,
    )
    agent.lecture_session = session  # used by ask() to save the cleaned answer
    return agent, index, trace, citations


def ask(job_dir: Path, student_id: str, question: str, language: str = "English") -> dict:
    """Answer one question. Returns the answer, its cited times and the tool calls made."""
    question = (question or "").strip()[:MAX_QUESTION_CHARS]
    if not question:
        raise ValueError("empty question")
    agent, index, trace, citations = _build(Path(job_dir), student_id, language)
    result = agent(question, limits={"turns": 8, "total_tokens": 150_000})
    answer = strip_thinking(str(result))

    # Steering gives the agent two chances to fix its citations. If it still
    # cites a time no tool showed it, remove that citation rather than show
    # the student a link that may point at the wrong moment.
    removed = []

    def keep_or_drop(match: re.Match) -> str:
        if match.group(1) in index.shown_times:
            return match.group(0)
        removed.append(match.group(1))
        return ""

    def drop_unverifiable(match: re.Match) -> str:
        bracket = match.group("bracket")
        if CITED_TIME.fullmatch(bracket):
            return match.group(0)  # a single time; already checked above
        removed.append(bracket)
        return ""

    # When a time is removed, the words that led up to it go too, so
    # "..., which starts at [00:01]." becomes "..." and not "..., which starts at."
    answer = re.sub(LEAD_IN + r"\s*" + CITED_TIME.pattern, keep_or_drop, answer)
    answer = re.sub(LEAD_IN + r"\s*(?P<bracket>" + ANY_TIME_BRACKET.pattern + ")", drop_unverifiable, answer).strip()

    # Code the model wrote itself (not seen on the board) is dropped with its
    # sentence, in case steering ran out of revisions before it was fixed.
    code_removed = unverified_code(answer, index)
    if code_removed:
        sentences = re.split(r"(?<=[.!?])\s+", answer)
        answer = " ".join(s for s in sentences if not unverified_code(s, index)).strip() or (
            "The teacher shows this in the video. Watch that part of the lecture for the exact code.")

    # Save the cleaned answer over the raw one, so a reloaded chat (history)
    # and the model's next turn never see a citation the student wasn't shown.
    history_cleaned = False
    session = getattr(agent, "lecture_session", None)
    if (removed or code_removed) and session is not None:
        cleaned = {"role": "assistant", "content": [{"text": answer}]}
        session.redact_latest_message(cleaned, agent)
        history_cleaned = True

    seen, cited = set(), []
    for label in CITED_TIME.findall(answer):
        if label not in seen:
            seen.add(label)
            cited.append({"label": label, "seconds": _parse_time(label)})
    return {
        "answer": answer,
        "citations": cited,
        "unverified_citations_removed": removed,
        "history_cleaned": history_cleaned,
        "unverified_code_removed": code_removed,
        "tools": trace.calls,
        "steering_corrections": citations.problems,
        "stop_reason": result.stop_reason,
    }


def history(job_dir: Path, student_id: str, language: str = "English") -> list[dict]:
    """The student's saved conversation, as question/answer pairs.

    Rebuilding the agent with the same session id restores its messages from
    disk; no model call is made.
    """
    agent, *_ = _build(Path(job_dir), student_id, language)
    turns: list[dict] = []
    for message in agent.messages:
        blocks = message.get("content", [])
        if any("toolResult" in b for b in blocks):
            continue  # tool output goes back to the model as a user message
        text = strip_thinking(" ".join(b["text"] for b in blocks if "text" in b))
        if not text:
            continue
        if message["role"] == "user" and text.startswith(GUIDANCE_PREFIX.strip()):
            continue  # a steering correction, not something the student typed
        if message["role"] == "user":
            turns.append({"question": text, "answer": ""})
        elif turns:
            turns[-1]["answer"] = text  # the last assistant text before the next question
    return [t for t in turns if t["answer"]]
