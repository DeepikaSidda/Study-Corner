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
  ├─ listen ─ audio ─► Amazon Transcribe ─► timestamped lines  S1, S2, S3 …
  │
  ├─ look ── frames ─► slide-change detection (code) ─► board reader agent (vision)
  │
  ├─ notes ─ note writer agent ─► citations checked in code ─► coverage measured
  │
  ├─ quiz ── QUIZ AGENT (tool loop)
  │            tools: list_sections · read_transcript · search_transcript · check_question
  │                                                                        │
  │            check_question = code checks ─► duplicate check ─► JUDGE AGENT
  │                             (quote really in the cited lines?)   (agent used as a tool)
  │            steering: may not stop until the quiz is full
  │            hooks: per-tool and model-call budget · Limits: turns and tokens
  │
  └─ render ─► notes.md + lecture.json

then, in the web app:

  ASK THE LECTURE ─ chat agent with the same lecture tools
                    steering: every [mm:ss] cited must be a time a tool showed it
                    session manager: each student's conversation is saved
```

The order of the main steps is fixed in Python, because it is known in
advance. Inside the quiz and chat steps, the agents decide what to read and
when; deterministic code decides what they are allowed to keep.

## How it uses Strands

| Strands feature | Where | What it does here |
|---|---|---|
| `Agent` + structured output | `agents.py` | board reader (vision), note writer and judge each return a fixed schema |
| `@tool` | `tools.py`, `quiz_agent.py` | `search_transcript`, `read_transcript`, `list_sections`, `read_board`, `check_question`, all bound to one lecture |
| Agent as a tool | `check_question` | calls the judge agent, so the quiz agent cannot mark its own homework |
| Hooks | `guards.py` `BudgetHook` | caps calls per tool and model calls per request; a cancelled call tells the agent why |
| Hooks | `guards.py` `TraceHook` | records every tool call, shown in the UI and the quality report |
| Steering (`steer_after_model`) | `QuizCompletion` | blocks the quiz agent from finishing before enough questions have passed |
| Steering (`steer_after_model`) | `CitationCheck` | rejects a chat answer that cites a time no tool showed the agent, or a range it cannot verify |
| `Limits` | quiz and chat agents | hard ceiling on turns and tokens per run |
| `FileSessionManager` | `chat.py` | saves each student's conversation; follow-ups work after a restart |
| `SlidingWindowConversationManager` | `chat.py` | keeps long chats inside the context window |
| Strands Evals | `tools/evaluate.py` | `OutputEvaluator` and `TrajectoryEvaluator` score the chat agent |

## Grounding: what code checks

- **Notes cite transcript line numbers.** Citations to lines that don't exist
  are removed. A key point, term or example with no valid source is dropped.
- **Every quiz question carries an evidence quote.** `check_question` confirms
  in code that the quote is in the cited lines (exact, or a near match
  tolerating a dropped word, never a paraphrase), that a multiple-choice answer
  is one of the options, and that the question does not test a fact already
  used. Only then does the judge agent check the lines prove the answer. The
  quiz agent sees every failure reason and fixes the question or picks another
  fact.
- **Chat citations are verified.** Each `[03:14]` must be the start time of a
  transcript line, a board time or a section start that a tool returned in that
  request. Steering sends the agent back up to twice; anything still
  unverifiable is removed before the student sees it.

## What you get

`output/<lecture>/notes.md`:

- Summary
- Key points, each with ▶ the time it was said
- Full notes, section by section, in lecture order
- Key terms and worked examples (when the teacher gave them)
- What was on the board, read from the video
- Quiz, then an answer key with the teacher's own words and "rewatch from 03:14"
- A quality report: coverage, checks passed, questions rewritten or dropped

`output/<lecture>/lecture.json` contains the same data for other tools. In the
web app, use **Show notes** or **Download PDF** instead (see below).

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
MOV, WEBM, MKV, MP3, WAV, M4A, up to 2 GB), choose how many quiz questions you
want, and press **Make my notes**. The web app is for English lectures: it
transcribes English and writes the notes, quiz and chat in English. (The
command line still has `--language` and `--notes-language`.) Each upload makes paid Transcribe and Bedrock calls on
the AWS account the server prints when it starts.

While a job runs, the page shows each step with its time, a count of verified
quiz questions, and a live activity feed. When it finishes, the video sits
next to a chapter list and a timeline, with the notes in tabs: Summary and key
points, Full notes, Key terms, Board (each slide image with its text), an
interactive Quiz, **Ask**, and a Quality report. Every ▶ time jumps the video
to that moment, and a wrong quiz answer offers "Rewatch from 00:40". Keys:
`1`–`7` switch tabs, `K` plays or pauses, `/` jumps to Ask. The moon/sun
button switches between dark and light mode, and the choice is remembered.

**Show notes** (next to the title) opens the whole set of notes and the quiz as
one readable page with a contents list, and buttons to copy all the text or
print it. **Download PDF** gives the same content as a PDF: notes first, then
the quiz on its own page, then the answer key. The PDF is built the first time
it is asked for and reused after that.

Models nearly always put the right answer first, so the quiz moves it to a
different letter in each question (the order is fixed per quiz).

**Ask** is a chat that answers only from this lecture: "where does carbon
dioxide get in?" → "through tiny holes called stomata ▶ 00:18". It shows which
tools it used, refuses questions the lecture doesn't cover, and remembers the
conversation, so "and where does the water come from?" works. Each browser
gets its own conversation; add `?student=<id>` to the page address to reopen
one on another device. Every question is a paid model call.

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
| Quiz agent, 8 questions | 8 of 8 verified from 8 checks; 5 `read_transcript` calls |
| Quiz agent, 12 questions (more than this short lecture holds) | 12 of 12 from 15 checks: 3 repeats of an already-used fact rejected, the agent then found new facts |
| Full run | 80 s for the 65 s lecture, including transcription |
| Cleanup | audio deleted from S3 and Transcribe job removed after each run |

### Chat agent, measured with Strands Evals

`tools/evaluate.py`: 7 student questions, each scored by `OutputEvaluator`
(correct, grounded, cited, refuses off-topic) and `TrajectoryEvaluator` (looked
it up first).

| Result | Value |
|---|---|
| Evaluations passed | 14 of 14, overall score 0.99 |
| In-scope answers with a verified ▶ citation | 5 of 5 |
| Off-topic questions refused (history, and a nearby biology topic) | 2 of 2 |
| Answers that looked the lecture up before answering | 7 of 7 |
| Unverifiable citations removed in this run | 0 |

### Problems that real runs found, and the fixes

- **Reworded "quotes".** The model quoted the teacher as saying *"Chlorophyll
  traps the energy of sunlight"*; the teacher said *"It traps…"*. It passed as a
  near match, so the answer key now shows the transcript sentence, never the
  model's version.
- **The same fact asked twice.** "What three things do plants need?" and
  "Which is NOT a requirement?" were both proven by the same sentence. The
  duplicate check now compares evidence, not only wording.
- **Unverifiable citations.** The chat agent once cited a range,
  `[00:11-00:37]`. Steering now rejects any time format it cannot check.
- **Steering messages in the chat history.** Strands stores steering guidance
  in the conversation as a user message, so the student's history showed
  "You cited times without reading the lecture" as if they had typed it.
  Guidance is now tagged and filtered out.

The sample is clean, synthetic speech with typed slides. Real classroom
recordings with background noise, accents and handwriting have not been
tested yet.

## Evaluate

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools\evaluate.py --job output\photosynthesis
```

The built-in cases are for the sample lecture. For another lecture, pass
`--cases cases.json` with your own questions and expected answers.

## Verified without AWS

- Slide detection finds all 5 sample slides at 2, 5 and 10 second sampling.
- The transcript parser reproduces all 15 narration sentences exactly, from
  both Transcribe formats.
- The quiz checks give the expected result on hand-made cases: exact quote,
  near match, invented quote, paraphrase, wrong segment, answer not an option,
  and the same fact reworded.
- Steering decisions were tested directly: the quiz agent is held back when
  the quiz is short, and a chat answer is rejected for an invented time, a
  range, or citing before reading.
- The Transcribe client was checked against stubbed AWS responses: it uploads,
  polls, downloads, and deletes the audio and the job on success and failure.

