"""Command line: python -m lecture_agent LECTURE.mp4 --out output/my-lecture"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lecture_agent",
        description="Turn a recorded lecture into notes, a summary, key points and a verified quiz.",
    )
    parser.add_argument("source", type=Path, help="lecture video or audio file")
    parser.add_argument("--out", type=Path, help="output folder (default: output/<file name>)")
    parser.add_argument("--language", default="en-US", help="spoken language for Transcribe, e.g. en-US, en-IN, hi-IN, or auto")
    parser.add_argument("--notes-language", default="English", help="language to write the notes and quiz in")
    parser.add_argument("--questions", type=int, default=10, help="number of quiz questions to aim for")
    parser.add_argument("--frame-every", type=float, default=10.0, help="seconds between sampled video frames")
    parser.add_argument("--change-threshold", type=float, default=0.02, help="share of pixels that must change to count as a new slide (0-1)")
    parser.add_argument("--max-frames", type=int, default=24, help="most board frames to read")
    parser.add_argument("--no-board", action="store_true", help="skip reading the board and slides")
    parser.add_argument("--no-judge", action="store_true", help="skip the judge agent (code checks still run)")
    parser.add_argument("--transcript", type=Path, help="use an existing Transcribe JSON instead of transcribing")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask before using the AWS account shown")
    args = parser.parse_args(argv)

    if not args.source.exists():
        parser.error(f"file not found: {args.source}")
    out_dir = args.out or Path("output") / args.source.stem

    from . import config, pipeline

    try:
        who = config.whoami()
    except Exception as exc:  # noqa: BLE001
        print(f"AWS credentials are not usable: {exc}", file=sys.stderr)
        return 2
    print(f"AWS account {who['account']} ({who['arn']}), region {who['region']}, model {who['model']}")
    # The account comes from whatever credentials are active, which may not be
    # the one you expect (an IDE can export its own keys). Confirm before
    # Transcribe, S3 or Bedrock are billed.
    if not args.yes:
        reply = input("This run will make paid calls to that account. Continue? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Stopped. Nothing was sent to AWS beyond the identity check.")
            return 1

    pipeline.run(
        args.source,
        out_dir,
        language=args.language,
        notes_language=args.notes_language,
        questions=args.questions,
        frame_every=args.frame_every,
        change_threshold=args.change_threshold,
        max_frames=args.max_frames,
        read_board=not args.no_board,
        judge=not args.no_judge,
        transcript_json=args.transcript,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
