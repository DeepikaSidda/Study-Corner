"""Structured outputs for every agent.

Class names deliberately have no leading underscore. Amazon Nova loops forever
on Strands structured output when the schema class name starts with one
(measured during the workshop; Claude is unaffected).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BoardReading(BaseModel):
    has_content: bool = Field(description="False if the frame shows no board, slide or written content")
    text: str = Field(default="", description="All readable text exactly as written, one line per line on the board")
    formulas: list[str] = Field(default_factory=list, description="Each formula or equation, copied exactly")
    diagram: str = Field(default="", description="One sentence describing any diagram, empty if none")


class KeyPoint(BaseModel):
    point: str = Field(description="One important idea from the lecture, in one sentence")
    segment_ids: list[int] = Field(description="Transcript segment numbers (the S numbers) this comes from")


class NoteSection(BaseModel):
    title: str = Field(description="Short heading for this part of the lecture")
    bullets: list[str] = Field(description="The notes for this part, as short clear bullet points")
    segment_ids: list[int] = Field(description="Transcript segment numbers this section covers")


class KeyTerm(BaseModel):
    term: str
    definition: str = Field(description="Meaning as the teacher explained it, in simple words")
    segment_ids: list[int]


class WorkedExample(BaseModel):
    title: str
    steps: list[str] = Field(description="The steps the teacher went through, in order")
    segment_ids: list[int]


class LectureNotes(BaseModel):
    title: str = Field(description="A clear title for the lecture")
    summary: str = Field(description="4 to 6 sentence summary of the whole lecture")
    key_points: list[KeyPoint]
    sections: list[NoteSection] = Field(description="Full notes, following the order of the lecture")
    key_terms: list[KeyTerm] = Field(default_factory=list)
    worked_examples: list[WorkedExample] = Field(default_factory=list)


class QuizQuestion(BaseModel):
    question: str
    kind: Literal["multiple_choice", "short_answer"]
    options: list[str] = Field(default_factory=list, description="Exactly 4 options for multiple_choice, empty for short_answer")
    answer: str = Field(description="The correct answer; for multiple_choice, identical to one of the options")
    explanation: str = Field(description="One or two sentences explaining why the answer is correct")
    evidence_quote: str = Field(description="8 to 30 words copied exactly from the transcript that prove the answer")
    segment_ids: list[int] = Field(description="Segment numbers the evidence quote comes from")


class QuizSet(BaseModel):
    questions: list[QuizQuestion]


class JudgeVerdict(BaseModel):
    supported: bool = Field(description="True only if the lecture lines alone prove the answer is correct")
    reason: str = Field(description="One sentence explaining the decision")
