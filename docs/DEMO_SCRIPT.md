# Study Corner: demo video script (target 2:45, limit 3:00)

## Before recording

1. Start the server: `.\.venv\Scripts\python.exe -m lecture_agent.web`, then open
   <http://127.0.0.1:8000>. Use **dark mode**, browser zoom **100%**, and close other tabs.
2. Use lectures that are **already made**, so there's no waiting on camera:
   - **Bro Code: For Loops in Python** (YouTube, captions): the main demo
   - **CSC111 Exception Handling** (upload): for the Board tab
   - **Complete AI Masterclass** (Prashant Kirad, Hindi): for the Hindi moment
3. Open a fresh chat for the Bro Code lecture. In a new browser profile the Ask tab starts empty.
4. Record at 1920×1080 with the Windows screen recorder (`Win + Alt + R`) or OBS.
   Speak slowly. You can cut pauses afterwards.

## Script

| Time | On screen | Say |
|---|---|---|
| 0:00–0:15 | Home page, headline "Watch less. Learn more." | "Every student has missed a class. The recording is an hour long, and the exam is on Friday. Study Corner turns any lecture video into notes you can trust, in minutes." |
| 0:15–0:35 | Click **YouTube link**, paste `https://youtu.be/KWgYha0clzw`. The preview shows "Plays from YouTube · notes from its captions". | "Paste a YouTube link. The app checks the licence with the YouTube Data API. This video has the standard licence, so it's never downloaded. It plays in YouTube's own player, and the notes come from its captions. Creative Commons videos are downloaded, so the slides can be read too." |
| 0:35–0:45 | *(Cut.)* Open the finished Bro Code lecture from **Your lectures**. | "A minute later, here's the lecture." |
| 0:45–1:05 | Summary tab. Click a **key point ▶ time**: the video jumps. Click a **chapter**. | "A summary, key points and chapters. Every point has a time. Click it, and the video jumps to the second the teacher said it." |
| 1:05–1:30 | **Quiz** tab. Pick a **wrong** answer: it shows the teacher's quote and "Rewatch from…". Then a right one. | "The quiz is written by a Strands agent, but every answer is checked twice: by code, that the quote is really in the transcript, and by a separate judge agent. Get one wrong, and it tells you exactly where to rewatch." |
| 1:30–1:55 | **Ask** tab. Type "How do I count backwards?", show the answer with ▶ times. Then "Who won the cricket world cup?", which is refused. | "Ask the lecture anything. The chat agent answers only from what the teacher said, and every time it cites is checked by code. Off-topic questions are refused." |
| 1:55–2:10 | Open **CSC111 Exception Handling**, **Board** tab, scroll through the code slides. | "For uploads and Creative Commons videos, a vision agent reads every new slide, code included." |
| 2:10–2:25 | Open the **Hindi AI Masterclass** lecture. Show the English summary and the credit line "…Hindi captions, translated to English with Amazon Translate". | "Lectures in Hindi, Tamil or Telugu come out as English notes, translated line by line with Amazon Translate, so every time still matches the video." |
| 2:25–2:35 | Home page, type **"for loop"** in the search box, click a result. | "Every lecture is stored in Amazon DynamoDB, so you can search across all of them and jump straight to the moment." |
| 2:35–2:50 | Click **Download PDF** and show it for a second. Then the README's "At a glance" table, or the "How it works" section. | "Notes as a page or a PDF. Built with Strands Agents on Amazon Bedrock, Transcribe, Translate and DynamoDB. Study Corner: watch less, learn more." |

## Tips

- Keep the mouse still while talking, and move it only to click.
- If something is slow, cut it. Don't wait on camera.
- Don't show the terminal, the `.env` file or your AWS account number.
- Upload to YouTube as **Unlisted**, and check the link works in a private window before you submit.
