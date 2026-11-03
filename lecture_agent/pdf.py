"""Build a printable PDF of the notes and quiz from lecture.json.

Uses fpdf2 (pure Python). A Unicode TrueType font is needed for anything
beyond basic Latin text, so the first available of these is used:
    LECTURE_PDF_FONT (+ LECTURE_PDF_FONT_BOLD), Segoe UI, Arial, DejaVu Sans.
Nirmala UI (Windows) or Noto Sans Devanagari is added as a fallback for
Hindi and other Indian scripts when present.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fpdf import FPDF

# fontTools warns about font tables it skips while subsetting ("MERG NOT
# subset"). The output is unaffected, so keep the server log clean.
logging.getLogger("fontTools").setLevel(logging.ERROR)

from .transcript import fmt_time

INK = (15, 23, 42)
MUTED = (100, 116, 139)
PRIMARY = (15, 118, 110)
SOFT = (227, 244, 241)
LINE = (226, 232, 240)
LETTERS = "ABCDEFGH"

_FONT_CANDIDATES = [
    (os.environ.get("LECTURE_PDF_FONT"), os.environ.get("LECTURE_PDF_FONT_BOLD")),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", None),
]
_FALLBACK_CANDIDATES = [
    "C:/Windows/Fonts/Nirmala.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
]


class NotesPDF(FPDF):
    def __init__(self, title: str):
        super().__init__(format="A4")
        self.doc_title = title
        self.set_margins(18, 18, 18)
        self.set_auto_page_break(True, margin=18)
        self.set_title(title)
        self.set_creator("Study Corner")
        self.family = self._load_fonts()

    def _load_fonts(self) -> str:
        for regular, bold in _FONT_CANDIDATES:
            if regular and Path(regular).exists():
                self.add_font("Body", "", regular)
                self.add_font("Body", "B", bold if bold and Path(bold).exists() else regular)
                fallbacks = []
                for n, path in enumerate(_FALLBACK_CANDIDATES):
                    if Path(path).exists():
                        name = f"Fallback{n}"
                        self.add_font(name, "", path)
                        self.add_font(name, "B", path)
                        fallbacks.append(name)
                if fallbacks:
                    self.set_fallback_fonts(fallbacks)
                return "Body"
        return "Helvetica"  # Latin-1 only; text is sanitised in `text()`

    def text_safe(self, value: str) -> str:
        if self.family == "Helvetica":
            return value.encode("latin-1", "replace").decode("latin-1")
        return value

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(self.family, "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, self.text_safe(f"{self.doc_title}  ·  Study Corner  ·  page {self.page_no()}"), align="C")

    # -------------------------------------------------------------- blocks
    def heading(self, label: str) -> None:
        if self.get_y() > self.h - 50:
            self.add_page()
        self.ln(4)
        self.set_font(self.family, "B", 14)
        self.set_text_color(*PRIMARY)
        self.cell(0, 8, self.text_safe(label), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*LINE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def subheading(self, label: str, time_label: str = "") -> None:
        if self.get_y() > self.h - 35:
            self.add_page()
        self.set_font(self.family, "B", 11.5)
        self.set_text_color(*INK)
        self.multi_cell(0, 6, self.text_safe(label + (f"   ({time_label})" if time_label else "")),
                        align="L", new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def para(self, value: str, size: float = 10.5, color=INK) -> None:
        self.set_font(self.family, "", size)
        self.set_text_color(*color)
        self.multi_cell(0, 5.6, self.text_safe(value), align="L", new_x="LMARGIN", new_y="NEXT")

    def bullet(self, value: str, marker: str = "•", indent: float = 3, time_label: str = "") -> None:
        self.set_font(self.family, "", 10.5)
        self.set_text_color(*INK)
        self.set_x(self.l_margin + indent)
        self.cell(6, 5.6, self.text_safe(marker))
        text = value + (f"  ({time_label})" if time_label else "")
        self.multi_cell(self.epw - indent - 6, 5.6, self.text_safe(text), align="L", new_x="LMARGIN", new_y="NEXT")
        self.ln(0.8)

    def box(self, label: str, value: str) -> None:
        self.set_fill_color(*SOFT)
        self.set_font(self.family, "B", 8.5)
        self.set_text_color(*PRIMARY)
        self.multi_cell(0, 7, self.text_safe(label.upper()), fill=True, align="L", new_x="LMARGIN", new_y="NEXT",
                        padding=(2, 4, 0, 4))
        self.set_font(self.family, "", 11)
        self.set_text_color(*INK)
        self.multi_cell(0, 6, self.text_safe(value), fill=True, align="L", new_x="LMARGIN", new_y="NEXT", padding=(0, 4, 4, 4))
        self.ln(3)


def build_pdf(data: dict, out_path: Path) -> Path:
    notes = data["notes"]
    metrics = data.get("metrics", {})
    starts = {s["id"]: s["start"] for s in data["segments"]}

    def at(ids) -> str:
        found = [starts[i] for i in ids or [] if i in starts]
        return fmt_time(min(found)) if found else ""

    pdf = NotesPDF(notes["title"])
    pdf.add_page()

    # Title block
    pdf.set_font(pdf.family, "B", 20)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 9, pdf.text_safe(notes["title"]), align="L", new_x="LMARGIN", new_y="NEXT")
    meta = []
    if metrics.get("lecture_seconds"):
        meta.append(f"Lecture length {fmt_time(metrics['lecture_seconds'])}")
    meta += [f"{len(notes['sections'])} sections", f"{len(data['quiz'])} quiz questions",
             time.strftime("Made %d %b %Y")]
    pdf.para("  ·  ".join(meta), size=9.5, color=MUTED)
    pdf.para("Times in brackets show where to rewatch in the video.", size=9.5, color=MUTED)
    if data.get("credit"):
        pdf.para(data["credit"], size=9.5, color=MUTED)
    pdf.ln(4)

    pdf.box("In short", notes["summary"])

    pdf.heading("Key points")
    for n, kp in enumerate(notes["key_points"], start=1):
        pdf.bullet(kp["point"], marker=f"{n}.", time_label=at(kp["segment_ids"]))

    pdf.heading("Full notes")
    for n, section in enumerate(notes["sections"], start=1):
        pdf.subheading(f"{n}. {section['title']}", at(section["segment_ids"]))
        for line in section["bullets"]:
            pdf.bullet(line)
        pdf.ln(2)

    if notes.get("key_terms"):
        pdf.heading("Key terms")
        for term in notes["key_terms"]:
            pdf.subheading(term["term"], at(term["segment_ids"]))
            pdf.para(term["definition"])
            pdf.ln(2)

    if notes.get("worked_examples"):
        pdf.heading("Worked examples")
        for example in notes["worked_examples"]:
            pdf.subheading(example["title"], at(example["segment_ids"]))
            for n, step in enumerate(example["steps"], start=1):
                pdf.bullet(step, marker=f"{n}.")
            pdf.ln(2)

    boards = [b for b in data.get("board", []) if b.get("has_content")]
    if boards:
        pdf.heading("On the board")
        for board in boards:
            pdf.subheading(f"At {fmt_time(board['time'])}")
            text = "\n".join(line.strip() for line in board["text"].splitlines() if line.strip())
            pdf.para(text)
            for formula in board.get("formulas", []):
                if formula.strip() not in board["text"]:
                    pdf.para(formula)
            if board.get("diagram"):
                pdf.para(f"Diagram: {board['diagram']}", color=MUTED)
            pdf.ln(2)

    if data["quiz"]:
        pdf.add_page()
        pdf.heading("Quiz")
        for n, q in enumerate(data["quiz"], start=1):
            pdf.subheading(f"{n}. {q['question']}")
            if q["kind"] == "multiple_choice":
                for letter, option in zip(LETTERS, q["options"]):
                    pdf.bullet(option, marker=f"{letter}.", indent=6)
            else:
                pdf.para("Answer: ______________________________", color=MUTED)
            pdf.ln(2)

        pdf.heading("Answer key")
        for n, q in enumerate(data["quiz"], start=1):
            answer = q["answer"]
            if q["kind"] == "multiple_choice":
                for letter, option in zip(LETTERS, q["options"]):
                    if option.strip().lower() == answer.strip().lower():
                        answer = f"{letter}. {option}"
            when = at(q["segment_ids"])
            pdf.subheading(f"{n}. {answer}")
            pdf.para(q["explanation"])
            pdf.para(f"The teacher said: \u201c{q['evidence_quote']}\u201d" + (f"  (rewatch from {when})" if when else ""),
                     size=9.5, color=MUTED)
            pdf.ln(2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))
    return out_path
