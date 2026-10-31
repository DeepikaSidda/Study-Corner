"""Build a short sample lecture video whose content is known exactly.

Slides are drawn with Pillow and narrated with the Windows built-in speech
voice, so it needs no AWS account and no copyrighted material. Because the
script knows every word and every slide time, the output can be checked
against ground truth.

Writes to data/sample/:
    photosynthesis.mp4          the lecture video
    ground_truth.json           slides, narration and timings
    transcript_synthetic.json   a transcript in Amazon Transcribe's JSON
                                format, built from the known narration.
                                SYNTHETIC: for offline testing only; real
                                runs use Amazon Transcribe.

Run:  python tools/make_sample_lecture.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lecture_agent.media import ffmpeg_exe  # noqa: E402

OUT = ROOT / "data" / "sample"
WIDTH, HEIGHT = 1280, 720

SLIDES = [
    {
        "title": "Photosynthesis",
        "lines": ["How plants make their own food"],
        "narration": (
            "Today we are learning about photosynthesis. Photosynthesis is the process "
            "plants use to make their own food. It happens mainly in the leaves of the plant."
        ),
    },
    {
        "title": "What plants need",
        "lines": ["1. Sunlight", "2. Water", "3. Carbon dioxide"],
        "narration": (
            "Plants need three things for photosynthesis. They need sunlight, water and carbon "
            "dioxide. Water comes up from the roots, and carbon dioxide enters through tiny holes "
            "in the leaf called stomata."
        ),
    },
    {
        "title": "The equation",
        "lines": ["6CO2 + 6H2O  ->  C6H12O6 + 6O2", "carbon dioxide + water -> glucose + oxygen"],
        "narration": (
            "Here is the equation on the board. Six molecules of carbon dioxide and six molecules "
            "of water make one molecule of glucose and six molecules of oxygen. The energy for "
            "this comes from sunlight."
        ),
    },
    {
        "title": "Chlorophyll",
        "lines": ["Green pigment in chloroplasts", "Traps energy from sunlight"],
        "narration": (
            "The green colour of leaves comes from chlorophyll. Chlorophyll is a pigment inside "
            "the chloroplasts. It traps the energy of sunlight so the plant can use it."
        ),
    },
    {
        "title": "Why it matters",
        "lines": ["Gives us oxygen to breathe", "Makes food for almost all life"],
        "narration": (
            "Photosynthesis gives us the oxygen we breathe. It also makes the food that almost "
            "every living thing depends on. Without photosynthesis there would be no life on "
            "Earth as we know it."
        ),
    },
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    for name in names:
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def draw_slide(slide: dict, path: Path) -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), (24, 61, 46))  # chalkboard green
    draw = ImageDraw.Draw(img)
    draw.text((80, 70), slide["title"], fill=(255, 255, 255), font=font(72, bold=True))
    draw.line((80, 175, WIDTH - 80, 175), fill=(220, 220, 200), width=3)
    y = 230
    for line in slide["lines"]:
        draw.text((100, y), line, fill=(240, 240, 220), font=font(48))
        y += 90
    img.save(path)


def speak(text: str, wav_path: Path) -> None:
    """Narrate with the Windows built-in voice (System.Speech)."""
    txt_path = wav_path.with_suffix(".txt")
    txt_path.write_text(text, encoding="utf-8")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{wav_path}'); "
        f"$s.Speak([IO.File]::ReadAllText('{txt_path}')); "
        "$s.Dispose()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], check=True)
    txt_path.unlink()


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / float(w.getframerate())


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def synthetic_transcript(timed: list[dict]) -> dict:
    """Transcribe-format JSON. Sentence times are spread across each slide's
    audio in proportion to word count, so they are close but not exact."""
    audio_segments, items = [], []
    seg_id = 0
    for slide in timed:
        parts = sentences(slide["narration"])
        total_words = sum(len(p.split()) for p in parts)
        t = slide["start"]
        span = slide["end"] - slide["start"]
        for part in parts:
            words = part.split()
            length = span * len(words) / total_words
            audio_segments.append(
                {"id": seg_id, "transcript": part, "start_time": f"{t:.3f}", "end_time": f"{t + length:.3f}"}
            )
            seg_id += 1
            step = length / len(words)
            for i, word in enumerate(words):
                clean = word.rstrip(".,!?")
                items.append(
                    {
                        "type": "pronunciation",
                        "start_time": f"{t + i * step:.3f}",
                        "end_time": f"{t + (i + 1) * step:.3f}",
                        "alternatives": [{"content": clean, "confidence": "1.0"}],
                    }
                )
                if word != clean:
                    items.append({"type": "punctuation", "alternatives": [{"content": word[len(clean):]}]})
            t += length
    return {
        "synthetic": True,
        "note": "Built from known narration for offline testing. Not real Amazon Transcribe output.",
        "results": {
            "transcripts": [{"transcript": " ".join(s["narration"] for s in timed)}],
            "items": items,
            "audio_segments": audio_segments,
        },
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / "build"
    work.mkdir(exist_ok=True)

    timed = []
    t = 0.0
    for i, slide in enumerate(SLIDES, start=1):
        png = work / f"slide{i}.png"
        wav = work / f"slide{i}.wav"
        draw_slide(slide, png)
        speak(slide["narration"], wav)
        seconds = wav_seconds(wav) + 0.6  # short pause before the next slide
        timed.append({**slide, "png": png, "wav": wav, "start": t, "end": t + seconds})
        t += seconds
        print(f"slide {i}: {seconds:.1f}s  {slide['title']}")

    # Silence for each slide's trailing pause, in exactly the narration's WAV
    # format, otherwise the concat demuxer misreads it.
    pause = work / "pause.wav"
    with wave.open(str(timed[0]["wav"])) as ref:
        params = ref.getparams()
    with wave.open(str(pause), "wb") as w:
        w.setparams(params)
        frames = int(params.framerate * 0.6)
        w.writeframes(b"\x00" * frames * params.sampwidth * params.nchannels)

    images = work / "images.txt"
    audio = work / "audio.txt"
    with images.open("w", encoding="utf-8") as fi, audio.open("w", encoding="utf-8") as fa:
        for slide in timed:
            fi.write(f"file '{slide['png'].as_posix()}'\nduration {slide['end'] - slide['start']:.3f}\n")
            fa.write(f"file '{slide['wav'].as_posix()}'\nfile '{pause.as_posix()}'\n")
        fi.write(f"file '{timed[-1]['png'].as_posix()}'\n")  # concat demuxer needs the last image repeated

    video = OUT / "photosynthesis.mp4"
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-y",
         "-f", "concat", "-safe", "0", "-i", str(images),
         "-f", "concat", "-safe", "0", "-i", str(audio),
         "-vf", "fps=10,format=yuv420p", "-c:v", "libx264", "-c:a", "aac", "-ar", "44100",
         "-t", f"{t:.3f}", str(video)],
        capture_output=True, text=True, errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr[-1500:])

    truth = [
        {"title": s["title"], "lines": s["lines"], "narration": s["narration"],
         "start": round(s["start"], 3), "end": round(s["end"], 3)}
        for s in timed
    ]
    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    (OUT / "transcript_synthetic.json").write_text(json.dumps(synthetic_transcript(timed), indent=2), encoding="utf-8")
    print(f"\nwrote {video} ({t:.1f}s), ground_truth.json, transcript_synthetic.json")


if __name__ == "__main__":
    main()
