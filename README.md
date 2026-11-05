# Study Corner

**Watch less. Learn more.**

Study Corner turns any lecture video into notes a student can trust: a
summary, full notes, key terms, what was on the board, a checked quiz, and a
chat that answers only from what the teacher said. Every point links to the
second of the video where it was said.

**Live demo: <https://54-225-36-174.sslip.io>**

Built with the [Strands Agents SDK](https://strandsagents.com) on AWS: Amazon
Bedrock (Nova Pro), Amazon Transcribe, Amazon Translate, Amazon DynamoDB and
Amazon S3.

---

## Contents

1. [The problem](#the-problem)
2. [The idea](#the-idea)
3. [What makes it different](#what-makes-it-different)
4. [What it does](#what-it-does)
5. [How it works](#how-it-works)
6. [Run it yourself](#run-it-yourself)
7. [Deploy it on AWS](#deploy-it-on-aws)
8. [Results on real lectures](#results-on-real-lectures)
9. [Cost, limits and safety](#cost-limits-and-safety)
10. [Credits](#credits)

---

## The problem

Every student misses a class sometimes: work, illness, family, a long trip to
college, or a lecture in a language they are still learning. The recording
exists, but it is an hour long and the exam is on Friday.

AI tools can summarise a video, but a student can't tell whether the summary
is right. A wrong summary is worse than none. In a quiz it is worse still: a
wrong "correct answer" teaches the wrong thing, and the student has no way to
check.

## The idea

Study Corner makes notes from a lecture with one rule: **every answer must be
traceable to the moment in the video where the teacher said it, and code,
not the AI model, decides whether it is.**

The student adds a lecture (a file or a YouTube link). Five AI agents read
the transcript and the slides and write the notes and quiz. Before anything
reaches the student, plain code checks the agents' work against the
transcript: quotes must really be in the cited lines, times must be times the
tools actually returned, and code shown in a chat answer must have been on
screen.

## What makes it different

| A typical AI summariser | Study Corner |
|---|---|
| Gives a summary you have to trust | Every key point, chapter and chat answer has a ▶ time; click it and the video jumps there |
| Quiz answers may be made up | Each quiz answer carries a quote from the teacher. Code checks the quote is in the transcript, then a separate **judge agent** checks it proves the answer. Failed questions are rewritten |
| Chat answers from general knowledge | The chat answers **only** from this lecture, cites times, and refuses off-topic questions ("Who won the cricket world cup?" is refused) |
| Listens only | Also **reads the board and slides**, including code on screen |
| English only | Hindi, Tamil and Telugu lectures become English notes; Amazon Translate keeps every line's time |
| Downloads any video | Checks the YouTube licence first. Creative Commons videos are downloaded; all others are **never downloaded**: they play in YouTube's own player and the notes come from the captions |
| Hides its mistakes | A **Quality** tab shows how much of the lecture the notes cover and how many quiz questions were rejected and why |

## What it does

For every lecture:

- **Summary and key points**, each with a ▶ time
- **Full notes** in chapters, with a timeline under the video
- **Key terms** and worked examples, when the teacher gave them
- **Board**: every new slide or board, with its text and code
- **Quiz**: multiple choice and short answer. A wrong answer shows the
  teacher's own words and "Rewatch from 01:30". The right answer is spread
  across A, B, C and D
- **Ask the lecture**: a chat that answers only from the lecture, cites
  times, remembers the conversation (follow-ups work), and refuses anything
  else
- **Show notes**: the whole lecture as one clean page with a contents list,
  copy and print
- **Download PDF**: notes, then the quiz, then the answer key
- **Quality**: coverage, checks passed, questions rewritten
- **Search** across every lecture from the home page, linking to the moment
  each word was said

Three ways to add a lecture:

| Input | How the words are found | Board and slides |
|---|---|---|
| **Upload** a video or audio file (MP4, MOV, WEBM, MKV, MP3, WAV, M4A, up to 2 GB) | Amazon Transcribe, which also detects the language. Non-English speech is translated with Amazon Translate | read |
| **YouTube link, Creative Commons** | downloaded at up to 720p, then the same as an upload | read |
| **Any other YouTube link** | never downloaded. Plays in YouTube's player; notes from YouTube's captions (translated if needed). If YouTube won't share the captions, the student pastes the transcript from "Show transcript" | not read |

The page also has dark and light mode, works on phones, and has keyboard
shortcuts: `1`–`7` switch tabs, `K` plays or pauses, `/` opens Ask.

## How it works

![Study Corner architecture: the student adds a lecture; Amazon Transcribe, Amazon Translate or YouTube captions give the timed words; five Strands agents run on Amazon Bedrock; everything is stored in Amazon DynamoDB](docs/architecture.png)

```
lecture (file or YouTube link)
  │
  ├─ 1. listen ── Amazon Transcribe (language detected) ─► timed lines S1, S2, S3 …
  │               or YouTube captions, for videos that may not be downloaded
  │               ─► Amazon Translate, if the lecture isn't in English (times kept)
  │
  ├─ 2. look ──── frames ─► slide-change detection (code) ─► BOARD READER agent (vision)
  │
  ├─ 3. notes ─── NOTE WRITER agent ─► citations checked in code ─► coverage measured
  │
  ├─ 4. quiz ──── QUIZ AGENT (tool loop)
  │                 tools: list_sections · read_transcript · search_transcript · check_question
  │                 check_question = code checks ─► duplicate check ─► JUDGE agent (agent as a tool)
  │                 steering: may not stop until the quiz is full
  │
  ├─ 5. store ─── Amazon DynamoDB: lecture, notes, quiz, transcript lines, board
  │
  └─ then, on the page:
     ASK THE LECTURE ─ CHAT agent with the same lecture tools
                       steering: every [mm:ss] must be a time a tool returned,
                       and code may only be shown if it was on screen
                       session manager: each student's conversation is saved
```

The order of the five steps is fixed in Python, because it never changes.
Inside the quiz and the chat, the agents decide what to read and when; code
decides what they are allowed to keep.

### How it uses Strands Agents

| Strands feature | Where | What it does here |
|---|---|---|
| `Agent` + structured output | `agents.py` | the board reader, note writer and judge each return a fixed schema |
| `BedrockModel` | `config.py` | every agent runs on Amazon Bedrock (Nova Pro) on one pinned AWS account |
| `@tool` | `tools.py`, `quiz_agent.py` | `search_transcript`, `read_transcript`, `list_sections`, `read_board`, `check_question`, all bound to one lecture |
| Agent as a tool | `check_question` | calls the judge agent, so the quiz agent can't approve its own questions |
| Steering | `guards.py` `QuizCompletion` | keeps the quiz agent working until enough questions have passed |
| Steering | `guards.py` `CitationCheck` | sends back chat answers that cite a time no tool returned, a range it can't verify, or code that wasn't on screen |
| Hooks | `guards.py` `BudgetHook`, `TraceHook` | cap calls per tool and per request; record every tool call for the Quality tab |
| `Limits` | quiz and chat agents | hard ceiling on turns and tokens per run |
| `FileSessionManager` | `chat.py` | saves each student's chat, so follow-ups work after a restart |
| `SlidingWindowConversationManager` | `chat.py` | keeps long chats within the model's context |
| Strands Evals | `tools/evaluate.py` | `OutputEvaluator` and `TrajectoryEvaluator` score the chat agent |

### What code checks

- **Notes cite transcript lines.** Citations to lines that don't exist are
  removed; a key point or term with no valid source is dropped.
- **Every quiz question carries an evidence quote.** `check_question`
  confirms in code that the quote is in the cited lines (exact, or a near
  match that tolerates a dropped word, never a paraphrase), that a
  multiple-choice answer is one of the options, and that the question doesn't
  test a fact already used. Only then does the judge agent decide whether the
  lines prove the answer. The answer key always shows the transcript's
  sentence, never the model's version of it.
- **Chat citations are verified.** Each `[03:14]` must be a time a tool
  returned in that request. Steering sends the agent back up to twice;
  anything still unverifiable is removed, with the words that led up to it,
  before the student sees it and before it is saved to the chat history.
- **Chat code is verified.** Code in an answer must match what the board
  reader saw on screen; otherwise the sentence is removed.

### Where data is stored

| Store | Holds |
|---|---|
| **Amazon DynamoDB** table `study-corner-lectures` (on-demand) | per lecture: details (`META`), notes (`NOTES`), one item per quiz question (`QUIZ#001`…), transcript line (`LINE#00001`…) and board reading (`BOARD#00001`…); an `INDEX` partition lists lectures newest first. The pages and search read from it |
| **Lecture folder** `output/web/<id>/` | the video, frame pictures, PDF and chat sessions (large files; the table stores the path) |
| **Amazon S3** | the audio, only for the minutes Transcribe needs it, then deleted |

## Run it yourself

### What you need

- **Python 3.10 or newer.** ffmpeg comes with the `imageio-ffmpeg` package.
- **An AWS account** in `us-east-1` with access to Amazon Bedrock (model
  `us.amazon.nova-pro-v1:0`, which needs no Marketplace subscription), Amazon
  Transcribe, Amazon Translate, Amazon DynamoDB and Amazon S3. The S3 bucket
  and the DynamoDB table are created on first use.
- **Optional, for YouTube links:** a YouTube Data API v3 key, and Node.js
  (yt-dlp needs a JavaScript runtime to download Creative Commons videos).

### 1. Install

```powershell
git clone https://github.com/DeepikaSidda/Study-Corner.git
cd Study-Corner
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

On macOS or Linux use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`.

### 2. Point it at your AWS account

```powershell
aws configure --profile my-profile          # if you haven't set up credentials yet
$env:LECTURE_AWS_PROFILE = "my-profile"
```

Setting a profile pins the account. Without one, boto3 uses whatever
credentials are active, and an IDE may have exported keys for a different
account. The server prints the account it uses when it starts.

### 3. Add the YouTube key (optional)

Create a file named `.env` in the project folder. It is git-ignored, so it
never reaches GitHub:

```
YOUTUBE_API_KEY=your-key
```

Without it, everything except YouTube links works.

### 4. Start the web app

```powershell
.\.venv\Scripts\python.exe -m lecture_agent.web
```

Open <http://127.0.0.1:8000>.

### 5. Use it

1. Drop a video on **Upload a video**, or paste a link into **YouTube link**.
   For a link, the page shows its title, length and how it will be handled
   before anything is charged.
2. Press **Make my notes**. The page shows each step live: listening,
   reading the board, writing notes, building the quiz (with a count of
   verified questions).
3. When it's done, use the tabs. Click any ▶ time to jump the video.
4. Use **Show notes** or **Download PDF** to keep the notes.
5. Use the search box under **Your lectures** to find a word in any lecture.

Lectures are processed one at a time, so uploads queue instead of
multiplying the cost. Pasting a YouTube link that was already made opens the
existing notes.

### Command line (no web page)

```powershell
.\.venv\Scripts\python.exe -m lecture_agent lecture.mp4 --questions 10
```

| Option | Meaning |
|---|---|
| `--language en-US` | spoken language for Transcribe (`en-IN`, `hi-IN`, … or `auto`) |
| `--notes-language English` | language to write the notes and quiz in |
| `--frame-every 10` | seconds between sampled frames |
| `--no-board` | skip reading the board |
| `--no-judge` | skip the judge agent; code checks still run |
| `--transcript file.json` | use an existing Transcribe result |
| `-y` | don't ask before using the AWS account |

It writes `notes.md` and `lecture.json` to `output/<lecture>/`. Transcripts
are cached there, so a rerun doesn't pay for Transcribe again.



## Deploy it on AWS

`deploy/` puts Study Corner on one Amazon EC2 server, which is how the live
demo runs:

```powershell
.\.venv\Scripts\python.exe deploy\check_access.py   # can your AWS user do this? (creates nothing)
.\.venv\Scripts\python.exe deploy\package.py        # code + lectures to S3; YouTube key to SSM Parameter Store
.\.venv\Scripts\python.exe deploy\launch.py         # IAM role, security group, t3.small server
.\.venv\Scripts\python.exe deploy\https.py          # HTTPS with Caddy + Let's Encrypt
```

| Script | Does |
|---|---|
| `check_access.py` | dry-run checks for every permission the deploy needs |
| `package.py` | uploads the committed code and finished lectures to S3; stores the YouTube key (and a demo passcode) in SSM Parameter Store as SecureStrings |
| `launch.py` | creates an IAM role with only what the app needs, a security group with no SSH, and an Amazon Linux 2023 server that installs the app and runs it as a systemd service |
| `https.py` | opens port 443 and puts Caddy in front of the app with a free certificate for `<ip-with-dashes>.sslip.io` ([sslip.io](https://sslip.io) turns the IP into a name); http redirects to https |
| `update.py` | ships the latest commit to the server and restarts it |
| `remote.py "<command>"` | runs a command on the server through AWS Systems Manager |
| `teardown.py --yes` | deletes the server, security group, role, parameters and S3 package (keeps DynamoDB) |

No key or password is stored in the code, the package or the server's
startup script; the server reads them from SSM Parameter Store when the app
starts. Don't stop and start the server from the console: it would get a new
IP address and the link would change.

## Results on real lectures

| Lecture | Way in | Result |
|---|---|---|
| *Exception Handling in Python* (Adam Gaweda, CSC111, CC BY 3.0), 5:07 | upload | 19 of 19 kept screens read, including code and tracebacks; 7 sections; 91% of the lecture cited; 7 verified questions from 13 checks |
| *Learn Python: Identifiers and Keywords* (Indently, CC BY), 4:04 | YouTube, downloaded | 7.4 MB in 12 s; 7 code screens read; 100% cited; 8/8 questions |
| *Learn Python for loops in 5 minutes!* (Bro Code, standard licence), 5:06 | YouTube captions | 60 s, nothing downloaded; 5 chapters; 8/8 questions from 11 checks |
| *Complete AI Masterclass for Students* (Prashant Kirad, Hindi), 15:38 | YouTube Hindi captions + Amazon Translate | 15,810 characters translated in 9 s; 99% cited; 10/10 questions; "What does RCTO stand for?" answered with ▶ 04:29 |
| *How to contribute to Hindi Wikipedia* (CC BY-SA 3.0), 4:58 | upload, Hindi speech | detected as hi-IN; 21 of 24 screens read with no failures; 5 chapters; 91% cited; 8/8 questions |

Chat agent, scored with Strands Evals on 7 student questions: **14 of 14
evaluations passed** (score 0.99). All in-scope answers had a verified ▶
citation, both off-topic questions were refused, and every answer looked the
lecture up first.





## Cost, limits and safety

**Cost.** Each lecture costs a few cents to tens of cents in Bedrock,
Transcribe and Translate calls; a YouTube-captions lecture skips Transcribe.
Each chat question is a fraction of a cent. DynamoDB is on-demand, so it
costs nothing when idle. The EC2 demo server is about $15–20 a month while it
runs; `deploy/teardown.py --yes` removes it.

**Limits.**

- Hindi and other non-Latin text on slides isn't shown in the Board tab;
  what the teacher says is still in the notes.
- Standard-licence YouTube videos don't get a Board tab, because the video
  is never downloaded.
- YouTube blocks caption requests from AWS servers, so on the live demo,
  standard-licence links ask for a pasted transcript. On your own computer
  the captions are read automatically.
- Search reads the whole table; fine for a class, not for a large library.




## Credits

Demo lectures:

- [“Exception Handling in Python with try-except”](https://commons.wikimedia.org/wiki/File:Exception_Handling_in_Python_with_try-except.webm), Adam Gaweda, CC BY 3.0
- [“Learn Python: Identifiers and Keywords (Lesson 2)”](https://www.youtube.com/watch?v=3tp32DBXE1g), Indently, CC BY
- [“How to contribute to Hindi Wikipedia”](https://commons.wikimedia.org/wiki/File:How_to_contribute_to_Hindi_Wikipedia.ogv), Subhashish Panigrahi and Noopur Raval, CC BY-SA 3.0
- Standard-licence videos (Bro Code, English At The Ready, Prashant Kirad) are
  not stored or downloaded: they play from YouTube, and their notes were made
  from YouTube's captions for testing.

Libraries: [Strands Agents](https://strandsagents.com),
[yt-dlp](https://github.com/yt-dlp/yt-dlp),
[youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api),
[Starlette](https://www.starlette.io), [fpdf2](https://py-pdf.github.io/fpdf2/),
[diagrams](https://diagrams.mingrammer.com), [Caddy](https://caddyserver.com).

Study Corner started in the AWS *Building AI Agents with Strands SDK*
workshop. The tools, agents as tools, steering, hooks, sessions and evals
from that workshop all do real work here.
