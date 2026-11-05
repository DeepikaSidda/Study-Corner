"""Deterministic controls around the agents: hooks and steering handlers.

The model decides what to write. These decide what is allowed, and they run
every time whatever the model reasons:

  BudgetHook           caps calls per tool and model calls per request, so a
                       looping agent cannot run up the bill
  TraceHook            records every tool call, for the UI and the metrics
  QuizCompletion       steering: the quiz agent may not stop until enough
                       questions have passed checking
  CitationCheck        steering: a chat answer may only cite times of
                       transcript lines the agent actually read
"""

from __future__ import annotations

import re
from typing import Any

from strands.hooks import (
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)
from strands.vended_plugins.steering import Guide, Proceed, SteeringHandler

from .transcript import fmt_time

THINKING = re.compile(r"<thinking>.*?(</thinking>|$)", re.S)
CITED_TIME = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")
# Any bracket that contains a time, including forms the check cannot verify
# such as ranges "[00:11-00:37]" or "[at 00:11]". A real run produced a range.
ANY_TIME_BRACKET = re.compile(r"\[[^\]\n]*\d{1,2}:\d{2}[^\]\n]*\]")
NOT_COVERED = "does not cover"
# Strands stores steering guidance in the conversation as a user message.
# The prefix lets the chat history tell it apart from what the student typed.
GUIDANCE_PREFIX = "[automatic check] "


def strip_thinking(text: str) -> str:
    """Nova writes its reasoning in <thinking> tags. Students should not see it."""
    return THINKING.sub("", text).strip()


def message_text(message: dict) -> str:
    return " ".join(b["text"] for b in message.get("content", []) if "text" in b)


class BudgetHook(HookProvider):
    """Caps tool calls (per tool) and model calls for each request."""

    def __init__(self, tool_limits: dict[str, int] | None = None, default_tool_limit: int = 20, max_model_calls: int = 40):
        self.tool_limits = tool_limits or {}
        self.default_tool_limit = default_tool_limit
        self.max_model_calls = max_model_calls
        self.reset(None)

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self.reset)
        registry.add_callback(BeforeToolCallEvent, self.check_tool)
        registry.add_callback(BeforeModelCallEvent, self.check_model)

    def reset(self, event) -> None:
        self.tool_calls: dict[str, int] = {}
        self.model_calls = 0
        self.blocked: list[str] = []

    def check_tool(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        self.tool_calls[name] = self.tool_calls.get(name, 0) + 1
        limit = self.tool_limits.get(name, self.default_tool_limit)
        if self.tool_calls[name] > limit:
            # The message goes back to the model as the tool result, so it
            # knows why and stops retrying.
            event.cancel_tool = f"'{name}' has reached its limit of {limit} calls. Do not call it again; finish with what you have."
            self.blocked.append(name)

    def check_model(self, event: BeforeModelCallEvent) -> None:
        self.model_calls += 1
        if self.model_calls > self.max_model_calls:
            event.cancel = f"Model call budget of {self.max_model_calls} reached."
            self.blocked.append("model")


class TraceHook(HookProvider):
    """Records each tool call: name, a short view of its input, and the outcome."""

    def __init__(self):
        self.calls: list[dict] = []

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, lambda e: self.calls.clear())
        registry.add_callback(AfterToolCallEvent, self.record)

    def record(self, event: AfterToolCallEvent) -> None:
        tool_input = event.tool_use.get("input", {}) or {}
        summary = ", ".join(f"{k}={str(v)[:40]}" for k, v in tool_input.items() if k != "options")
        result = event.result or {}
        text = " ".join(c.get("text", "") for c in result.get("content", []))
        self.calls.append({
            "tool": event.tool_use["name"],
            "input": summary[:160],
            "status": result.get("status"),
            "result": text[:120],
        })


class QuizCompletion(SteeringHandler):
    """The quiz agent may not finish until the quiz has enough verified questions."""

    name = "quiz-completion"

    def __init__(self, book, max_nudges: int = 3):
        super().__init__(context_providers=[])
        self.book = book
        self.max_nudges = max_nudges
        self.nudges = 0

    async def steer_after_model(self, *, agent: Any, message: dict, stop_reason: str, **kwargs: Any):
        if stop_reason != "end_turn" or self.book.complete:
            return Proceed(reason="quiz complete or still working")
        if self.nudges >= self.max_nudges:
            # A short lecture may not hold enough separate facts. A shorter
            # quiz of verified questions beats padding it with repeats.
            return Proceed(reason="nudge limit reached; accepting a shorter quiz")
        self.nudges += 1
        return Guide(
            reason=GUIDANCE_PREFIX + (
                f"Only {len(self.book.accepted)} of {self.book.target} questions have passed check_question. "
                "Keep going: read another part of the lecture, write a new question and call check_question. "
                "Stop only when check_question says the quiz is complete."
            )
        )


# Code in a chat answer: `inline code`, ```blocks```, or a bare call like range(10, 0).
CODE_SPAN = re.compile(r"```(?:\w+\n)?(.*?)```|`([^`\n]+)`", re.S)
CODE_CALL = re.compile(r"\b[A-Za-z_]\w*\((?:[^()\n]|\([^()\n]*\))*\)")


def _squash(text: str) -> str:
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", "", text).lower()


def unverified_code(text: str, index) -> list[str]:
    """Code in an answer that was not on the board or slides, so the model wrote it itself.

    A real answer about counting backwards gave `reversed(range(10, 0))`, which
    is an empty loop in Python. The teacher had only said it out loud. Code can
    only be shown if read_board saw it, copied as it was.
    """
    board = _squash(" ".join(" ".join([b.get("text", ""), b.get("code", "") or "", b.get("output", "") or "",
                                       " ".join(b.get("formulas", []))])
                             for b in getattr(index, "boards", []) or []))
    found = [a or b for a, b in CODE_SPAN.findall(text)]
    found += CODE_CALL.findall(CODE_SPAN.sub(" ", text))
    out = []
    for code in found:
        code = code.strip()
        if code and _squash(code) not in board and code not in out:
            out.append(code)
    return out


class CitationCheck(SteeringHandler):
    """A chat answer must cite times, and only times the tools showed the agent.

    Plain code, not a model: it compares each [mm:ss] in the answer with the
    times the tools returned during this request (transcript line starts and
    board times). An invented time cannot pass, and neither can a format the
    check cannot verify, such as a range.
    """

    name = "citation-check"

    def __init__(self, index, max_revisions: int = 2):
        super().__init__(context_providers=[])
        self.index = index
        self.max_revisions = max_revisions
        self.revisions = 0
        self.problems: list[str] = []

    def _reject(self, reason: str):
        self.revisions += 1
        self.problems.append(reason)
        return Guide(reason=GUIDANCE_PREFIX + reason)

    async def steer_after_model(self, *, agent: Any, message: dict, stop_reason: str, **kwargs: Any):
        if stop_reason != "end_turn":
            return Proceed(reason="not a final answer")
        text = strip_thinking(message_text(message))
        if not text or self.revisions >= self.max_revisions:
            return Proceed(reason="nothing to check or revision limit reached")

        brackets = ANY_TIME_BRACKET.findall(text)
        unverifiable = [b for b in brackets if not CITED_TIME.fullmatch(b)]
        if unverifiable:
            return self._reject(
                f"Write each citation as one time on its own, exactly as the tools show it, for example [03:14]. "
                f"These cannot be checked: {', '.join(unverifiable[:3])}."
            )

        made_up = unverified_code(text, self.index)
        if made_up:
            return self._reject(
                f"Your answer shows code that is not on the board or slides: {', '.join(made_up[:3])}. "
                "Don't write code yourself. Explain in words what the teacher said, and cite the time "
                "so the student can see the real code in the video."
            )

        cited = CITED_TIME.findall(text)
        if not cited:
            if NOT_COVERED in text.lower() or "?" in text[-3:]:
                return Proceed(reason="out of scope, or a clarifying question")
            return self._reject(
                "Your answer has no time citations. Search the transcript, then cite each fact with the "
                "time of the line it came from, like [03:14]. If the lecture does not cover the question, "
                'say "The lecture does not cover this."'
            )
        allowed = self.index.shown_times
        if not allowed:
            return self._reject("You cited times without reading the lecture. Call search_transcript first.")
        invented = [t for t in cited if t not in allowed]
        if invented:
            shown = ", ".join(sorted(allowed)[:15])
            return self._reject(
                f"You cited {', '.join(invented)}, but the tools never showed you anything at that time. "
                f"Times you were shown: {shown}. Cite only those."
            )
        return Proceed(reason="every cited time is one the tools showed the agent")
