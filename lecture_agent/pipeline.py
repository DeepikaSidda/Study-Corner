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
    source: Path | None,
    out_dir: Path,
    *,
    segments: list | None = None,
    lecture_seconds: float | None = None,
    transcript_label: str = "",
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
    """Make notes and a quiz from a media file, or from transcript lines that
    are already timed (`segments`, e.g. YouTube captions; no media needed)."""
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    if segments is not None:
        lecture_seconds = lecture_seconds or (segments[-1].end if segments else 0.0)
        log(f"lecture: {transcript_label or 'transcript'} ({lecture_seconds:.0f}s)")
    else:
        lecture_seconds = media.media_duration(source)
        log(f"lecture: {source.name} ({lecture_seconds:.0f}s)")

    # ---------------------------------------------------------------- listen
    raw_path = out_dir / "transcribe.json"
    if segments is not None:
        log(f"listen: using {transcript_label or 'the given transcript'}")
    elif transcript_json:
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
    spoken = None
    if segments is None:
        segments = parse_transcribe_json(raw)
        spoken = raw.get("detected_language") or (language if language != "auto" else None)
        if spoken:
            log(f"listen: spoken language {spoken}")
    if not segments:
        raise ValueError("The transcript is empty; is there speech in this recording?")

    # A lecture in another language (e.g. Hindi) becomes English line by line
    # with Amazon Translate, times unchanged, so notes, quiz checks and chat
    # citations all work on the same English lines.
    if spoken and not spoken.lower().startswith("en") and notes_language.lower().startswith("english"):
        english_path = out_dir / "segments.en.json"
        save_segments(segments, out_dir / "segments.original.json")
        if english_path.exists():
            from .transcript import Segment

            segments = [Segment(**r) for r in json.loads(english_path.read_text(encoding="utf-8"))]
            log("translate: reusing the saved English translation")
        else:
            from . import translate

            segments = translate.to_english(segments, spoken, log=log)
            save_segments(segments, english_path)
        transcript_label = transcript_label or f"Amazon Transcribe ({spoken}), translated to English with Amazon Translate"
    save_segments(segments, out_dir / "segments.json")
    log(f"listen: {len(segments)} segments")
    board_to_english = bool(spoken and not spoken.lower().startswith("en")
                            and notes_language.lower().startswith("english"))

    # ---------------------------------------------------------------- look
    boards = []
    sampled = kept = 0
    if read_board and source is not None and media.has_video(source):
        frames = media.sample_frames(source, out_dir / "frames", every_seconds=frame_every)
        changed = media.slide_changes(frames, threshold=change_threshold, max_frames=max_frames)
        sampled, kept = len(frames), len(changed)
        log(f"look: {sampled} frames sampled, {kept} kept after change detection")
        last_key = None
        for frame in changed:
            # One unreadable frame should not lose the whole lecture: skip it
            # (the transcript still covers what was said) and say so.
            try:
                reading = agents.read_board(frame, to_english=board_to_english, language=spoken or "auto")
                # The same code shown again a few seconds later isn't a new board.
                key = agents.board_key(reading) if reading.has_content else None
                if key and key == last_key:
                    reading = reading.model_copy(update={"has_content": False})
                elif key:
                    last_key = key
                boards.append((frame, reading))
            except Exception as exc:  # noqa: BLE001
                log(f"  look: skipped the frame at {fmt_time(frame.time)} ({type(exc).__name__}: {str(exc)[:80]})")
        log(f"look: {sum(1 for _, r in boards if r.has_content)} frames had board content")
    elif source is None:
        log("look: skipped, the video stays on YouTube")
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
        "transcript_source": transcript_label or ("Amazon Transcribe" if not transcript_json else "given transcript"),
        "processing_seconds": round(time.time() - started, 1),
        "frames_sampled": sampled,
        "frames_kept": kept,
        "notes": notes_stats,
        "quiz": quiz_stats,
    }
    md_path, json_path = write_outputs(out_dir, notes, quiz, boards, segments, metrics)
    log(f"done in {metrics['processing_seconds']:.0f}s -> {md_path}")
    return {"notes_md": md_path, "lecture_json": json_path, "metrics": metrics}
