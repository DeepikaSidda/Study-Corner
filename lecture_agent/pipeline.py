"""The pipeline: video in, notes and verified quiz out.

    listen   audio -> Amazon Transcribe -> timestamped segments
    look     frames -> slide-change detection -> board reader agent
    notes    note writer agent -> citations cleaned and coverage measured
    quiz     quiz agent: reads the lecture with tools, submits each question
             to check_question (code checks + judge agent), fixes failures
    render   notes.md + lecture.json

The order is fixed in code. Each step's model output is checked by code
before the next step relies on it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import agents, media
from .checker import clean_notes
from .quiz_agent import build_quiz
from .render import write_outputs
from .transcript import fmt_time, parse_transcribe_json, save_segments


def run(
    source: Path,
    out_dir: Path,
    *,
    language: str = "en-US",
    notes_language: str = "English",
    questions: int = 10,
    frame_every: float = 10.0,
    change_threshold: float = 0.02,
    max_frames: int = 24,
    read_board: bool = True,
    judge: bool = True,
    transcript_json: Path | None = None,
    log=print,
) -> dict:
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    lecture_seconds = media.media_duration(source)
    log(f"lecture: {source.name} ({lecture_seconds:.0f}s)")

    # ---------------------------------------------------------------- listen
    raw_path = out_dir / "transcribe.json"
    if transcript_json:
        raw = json.loads(Path(transcript_json).read_text(encoding="utf-8"))
        log(f"listen: using transcript {transcript_json}")
    elif raw_path.exists():
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        log(f"listen: reusing cached {raw_path} (delete it to transcribe again)")
    else:
        from .transcribe import transcribe

        if not media.has_audio(source):
            raise ValueError(f"{source} has no audio track")
        audio = media.extract_audio(source, out_dir / "audio.flac")
        log("listen: Amazon Transcribe")
        raw = transcribe(audio, language=language, log=log)
        raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    segments = parse_transcribe_json(raw)
    if not segments:
        raise ValueError("The transcript is empty; is there speech in this recording?")
    save_segments(segments, out_dir / "segments.json")
    log(f"listen: {len(segments)} segments")

    # ---------------------------------------------------------------- look
    boards = []
    sampled = kept = 0
    if read_board and media.has_video(source):
        frames = media.sample_frames(source, out_dir / "frames", every_seconds=frame_every)
        changed = media.slide_changes(frames, threshold=change_threshold, max_frames=max_frames)
        sampled, kept = len(frames), len(changed)
        log(f"look: {sampled} frames sampled, {kept} kept after change detection")
        for frame in changed:
            # One unreadable frame should not lose the whole lecture: skip it
            # (the transcript still covers what was said) and say so.
            try:
                boards.append((frame, agents.read_board(frame)))
            except Exception as exc:  # noqa: BLE001
                log(f"  look: skipped the frame at {fmt_time(frame.time)} ({type(exc).__name__}: {str(exc)[:80]})")
        log(f"look: {sum(1 for _, r in boards if r.has_content)} frames had board content")
    else:
        log("look: skipped")

    # ---------------------------------------------------------------- notes
    log("notes: writing")
    notes = agents.write_notes(segments, agents.format_boards(boards), notes_language)
    notes_stats = clean_notes(notes, segments)
    log(f"notes: {len(notes.sections)} sections, coverage {notes_stats['coverage']:.0%}")

    # ---------------------------------------------------------------- quiz
    log(f"quiz: agent building {questions} questions")
    quiz, quiz_stats = build_quiz(segments, notes, questions, notes_language, use_judge=judge, log=log)
    log(
        f"quiz: {len(quiz)} verified questions from {quiz_stats['checks']} checks "
        f"({quiz_stats['code_failures']} failed code checks, {quiz_stats['judge_failures']} failed the judge)"
    )

    # ---------------------------------------------------------------- render
    metrics = {
        "lecture_seconds": lecture_seconds,
        "processing_seconds": round(time.time() - started, 1),
        "frames_sampled": sampled,
        "frames_kept": kept,
        "notes": notes_stats,
        "quiz": quiz_stats,
    }
    md_path, json_path = write_outputs(out_dir, notes, quiz, boards, segments, metrics)
    log(f"done in {metrics['processing_seconds']:.0f}s -> {md_path}")
    return {"notes_md": md_path, "lecture_json": json_path, "metrics": metrics}
