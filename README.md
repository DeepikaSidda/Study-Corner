# Lecture Notes Agent

Turns a recorded lecture into **full notes, a summary, key points, key terms
and a quiz**, for students who missed the class. Every quiz answer is checked
against the teacher's actual words, and every note links to the minute of the
video where it was said.

Built with the [Strands Agents SDK](https://strandsagents.com) and Amazon
Bedrock, Amazon Transcribe and Amazon S3.

## Who it is for

Students who miss lectures because they work, care for family or travel far
to school, and students taught in a language they are still learning. A
missed hour of video becomes notes they can read in a few minutes, and a quiz
tells them which part to rewatch.

## How it works

```
lecture.mp4
  │
  ├─ listen ─ audio ─► Amazon Transcribe ─► timestamped sentences  S1, S2, S3 …
  │
  ├─ look ── frames every N s ─► slide-change detection ─► board reader agent
  │                              (code: keeps only frames     (reads text, formulas,
  │                               where the board changed)      diagrams)
  │
  ├─ notes ─ note writer agent ─► citations checked in code ─► coverage measured
  │
  ├─ quiz ── quiz writer agent ─► code checks ─► judge agent ─► rewrite loop
  │                               │ quote really in the cited lines?
  │                               │ answer is one of the options?
  │                               └ failures go back with the reasons, max 2 rounds
  │
  └─ render ─► notes.md  +  lecture.json
```

The order of steps is fixed in Python, not chosen by a model. Every model
output is checked by code before the next step relies on it:

- **Each note cites transcript sentence numbers.** Citations to sentences that
  don't exist are removed. A key point, term or example with no valid source
  is dropped, because it may be something the model added.
- **Every quiz question carries an evidence quote.** Code checks that the
  quote appears in the cited sentences (exact, or a near match tolerating a
  dropped word, but not a paraphrase). Only questions that pass go to the judge
  agent, which checks the cited lines prove the answer. Questions that fail are
  rewritten with the reasons, and anything still failing after two rounds is
  dropped. The quiz that ships contains only questions that passed both checks.

## What you get

`output/<lecture>/notes.md`:

- Summary
- Key points, each with ▶ the time it was said
- Full notes, section by section, in lecture order
- Key terms and worked examples (when the teacher gave them)
- What was on the board, read from the video
- Quiz, then an answer key with the teacher's own words and "rewatch from 03:14"
- A quality report: coverage, checks passed, questions rewritten or dropped

`output/<lecture>/lecture.json` contains the same data for other tools.

## Setup

Python 3.10+. ffmpeg comes bundled through `imageio-ffmpeg`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

AWS needs Bedrock (model `us.amazon.nova-pro-v1:0` by default), Transcribe
and S3 in `us-east-1`.

```powershell
$env:LECTURE_AWS_PROFILE = "my-profile"   # recommended: pins the account
```

Set a profile explicitly. Without one, boto3 uses whatever credentials are
active, and an IDE may have exported keys for a different account. The CLI
prints the account it found and **asks before making any paid call**.

| Variable | Default | Meaning |
|---|---|---|
| `LECTURE_AWS_PROFILE` | none | named AWS profile to use |
| `LECTURE_AWS_REGION` | `us-east-1` | region |
| `LECTURE_MODEL_ID` | `us.amazon.nova-pro-v1:0` | Bedrock model; must accept images |
| `LECTURE_MAX_TOKENS` | `8000` | output limit per model call |
| `LECTURE_S3_BUCKET` | `lecture-notes-agent-<account>-<region>` | where audio is uploaded for Transcribe; created if missing |

## Web app: upload a recorded lecture

```powershell
$env:LECTURE_AWS_PROFILE = "my-profile"
.\.venv\Scripts\python.exe -m lecture_agent.web
```

Open <http://127.0.0.1:8000>, choose or drag in a video or audio file (MP4,
MOV, WEBM, MKV, MP3, WAV, M4A, up to 2 GB), pick the spoken language, tick
the box confirming the run may spend on the AWS account shown, and press
**Make notes**.

The job page shows each step while it runs. When it finishes, the video sits
next to the notes in tabs: Summary and key points, Full notes, Key terms,
Board (each slide image with its text), an interactive Quiz, and a Quality
report. Every ▶ time jumps the video to that moment, and a wrong quiz answer
offers "Rewatch from 00:40". Notes download as `.md` or `.json`.

Jobs run one at a time so uploads queue instead of multiplying the spend.
Each job is kept in `output/web/<job id>/` and stays listed after a restart.

The server listens on 127.0.0.1 only and has no login. Don't expose it to the
internet as it is: anyone who can reach it could run jobs on your AWS account.

## Command line

```powershell
.\.venv\Scripts\python.exe -m lecture_agent lecture.mp4 --questions 10
```

| Option | Meaning |
|---|---|
| `--language en-US` | spoken language for Transcribe (`en-IN`, `hi-IN`, … or `auto`) |
| `--notes-language English` | language to write notes and quiz in |
| `--frame-every 10` | seconds between sampled frames |
| `--no-board` | skip reading the board |
| `--no-judge` | skip the judge agent; code checks still run |
| `--transcript file.json` | use an existing Transcribe result |
| `-y` | do not ask before using the AWS account |

Transcription results are cached in the output folder, so a rerun does not pay
for Transcribe again.

## Sample lecture

```powershell
.\.venv\Scripts\python.exe tools\make_sample_lecture.py
```

Builds `data/sample/photosynthesis.mp4`: a 65-second, 5-slide lecture
narrated by the Windows built-in voice. Because every word and slide time is
known, results can be checked against `ground_truth.json`. It also writes
`transcript_synthetic.json`, a transcript in Transcribe's format built from the
known narration, for testing without AWS. It is synthetic, not real
Transcribe output.

## Results on the sample lecture

Real run with Amazon Transcribe and Amazon Nova Pro, checked against the known
ground truth:

| Check | Result |
|---|---|
| Transcribe | 150 of 150 words heard; the only differences are numbers written as digits ("six" → "6") and US spelling |
| Section timestamps vs real slide starts | all 5 within 0.1 s |
| Board reading vs real slide text | 5 of 5 slides read 100% correctly, including the equation |
| Notes coverage | 14 of 14 transcript segments cited |
| Quiz | 8 of 8 questions verified; evidence quotes found word for word |
| Time | 82 s with transcription, 53 s when the transcript is cached, for a 65 s lecture |
| Cleanup | audio deleted from S3 and Transcribe job removed after the run |

One problem found in the first real run and fixed: the model "quoted" the
teacher as saying *"Chlorophyll traps the energy of sunlight"* when the teacher
said *"It traps the energy of sunlight"*. That is close enough to pass as a
near match, but it is not the teacher's words. The answer key now shows the
matching sentence from the transcript, never the model's version, and the
quality report counts how often that happened.

The sample is clean, synthetic speech with typed slides. Real classroom
recordings with background noise, accents and handwriting have not been
tested yet.

## Verified without AWS

- Slide detection finds all 5 sample slides at 2, 5 and 10 second sampling.
- The transcript parser reproduces all 15 narration sentences exactly, from
  both Transcribe formats.
- The quiz checker gives the expected result on 10 hand-made cases (exact
  quote, near match, invented quote, paraphrase, wrong segment, answer not an
  option and others), and the rewrite loop recovers failed questions.
- The full pipeline runs end to end with stand-in agents and writes both files.
- The Transcribe client was checked against stubbed AWS responses: it uploads,
  polls, downloads, and deletes the audio and the job on success and failure.

