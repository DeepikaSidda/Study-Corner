// Job page: show progress while the agent runs, then the notes beside the video.
// Everything from the model is inserted with textContent, never as HTML.

const jobId = document.body.dataset.job;
const base = `/jobs/${jobId}`;
let player = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function fmt(seconds) {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function seek(seconds) {
  if (!player) return;
  player.currentTime = seconds;
  player.play().catch(() => {});
  player.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function jump(seconds, label = "▶") {
  if (seconds === null || seconds === undefined) return null;
  return el("button", { class: "jump", title: "Play from here", onclick: () => seek(seconds) }, `${label} ${fmt(seconds)}`);
}

// ------------------------------------------------------------------ progress
const STEP_ORDER = ["listen", "look", "notes", "quiz", "done"];

function currentStep(log) {
  let step = "listen";
  for (const line of log) {
    for (const name of STEP_ORDER) if (line.includes(`  ${name}:`)) step = name;
    if (line.includes("done in")) step = "done";
  }
  return step;
}

async function poll() {
  let job;
  try {
    job = await (await fetch(`/api/jobs/${jobId}`)).json();
  } catch {
    setTimeout(poll, 4000);
    return;
  }
  document.getElementById("log").textContent = job.log.join("\n");
  const step = currentStep(job.log);
  document.querySelectorAll("#steps li").forEach(li => {
    const i = STEP_ORDER.indexOf(li.dataset.step), now = STEP_ORDER.indexOf(step);
    li.className = i < now ? "done" : i === now ? "active" : "";
  });

  if (job.status === "queued") {
    document.getElementById("status-title").textContent =
      `Waiting for another lecture to finish (position ${job.queue_position})…`;
    setTimeout(poll, 3000);
  } else if (job.status === "running") {
    document.getElementById("status-title").textContent = "Working on your lecture…";
    setTimeout(poll, 2000);
  } else if (job.status === "failed") {
    document.getElementById("status-title").textContent = "Something went wrong";
    const err = document.getElementById("error");
    err.hidden = false;
    err.textContent = job.error || "The job failed.";
  } else if (job.status === "done") {
    showResult(job);
  }
}

// ------------------------------------------------------------------ results
async function showResult(job) {
  const data = await (await fetch(`${base}/files/lecture.json`)).json();
  document.getElementById("progress-view").hidden = true;
  document.getElementById("result").hidden = false;

  const attrs = { controls: "", preload: "metadata", src: `${base}/files/media` };
  if (job.kind !== "audio" && data.board.length) attrs.poster = `${base}/frames/${data.board[0].frame}`;
  const media = el(job.kind === "audio" ? "audio" : "video", attrs);
  document.getElementById("media").append(media);
  player = media;

  const startOf = new Map(data.segments.map(s => [s.id, s.start]));
  const at = ids => {
    const starts = (ids || []).map(id => startOf.get(id)).filter(t => t !== undefined);
    return starts.length ? Math.min(...starts) : null;
  };
  const notes = data.notes;
  document.getElementById("lecture-title").textContent = notes.title;

  const panes = {
    "Summary": () => [
      el("p", { class: "summary" }, notes.summary),
      el("h3", {}, "Key points"),
      el("ul", { class: "points" }, notes.key_points.map(k => el("li", {}, k.point, " ", jump(at(k.segment_ids))))),
    ],
    "Full notes": () => notes.sections.map((s, i) =>
      el("article", { class: "section" },
        el("h3", {}, `${i + 1}. ${s.title} `, jump(at(s.segment_ids))),
        el("ul", {}, s.bullets.map(b => el("li", {}, b))))),
    "Key terms": () => {
      const out = [];
      if (notes.key_terms.length) {
        out.push(el("dl", { class: "terms" }, notes.key_terms.flatMap(t => [
          el("dt", {}, t.term, " ", jump(at(t.segment_ids))), el("dd", {}, t.definition)])));
      }
      for (const ex of notes.worked_examples) {
        out.push(el("h3", {}, `Worked example: ${ex.title} `, jump(at(ex.segment_ids))));
        out.push(el("ol", {}, ex.steps.map(step => el("li", {}, step))));
      }
      return out.length ? out : [el("p", { class: "muted" }, "The teacher did not define terms or work through examples.")];
    },
    "Board": () => {
      const boards = data.board.filter(b => b.has_content);
      if (!boards.length) return [el("p", { class: "muted" }, "Nothing was read from the board or slides.")];
      return boards.map(b => el("figure", { class: "board" },
        el("img", { src: `${base}/frames/${b.frame}`, alt: `Board at ${fmt(b.time)}`, loading: "lazy" }),
        el("figcaption", {}, jump(b.time),
          el("pre", {}, b.text.replace(/\n\s*\n/g, "\n").trim()),
          b.formulas.filter(f => !b.text.includes(f)).map(f => el("code", {}, f)),
          b.diagram ? el("p", {}, `Diagram: ${b.diagram}`) : null)));
    },
    "Quiz": () => quiz(data.quiz, at),
    "Ask": () => askPane(),
    "Quality": () => quality(data.metrics),
  };

  const tabs = document.getElementById("tabs");
  const holder = document.getElementById("panes");
  // Each pane is built once and kept, so quiz answers and the chat survive
  // switching tabs.
  const built = {};
  const show = name => {
    tabs.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.textContent === name));
    if (!built[name]) built[name] = el("div", {}, panes[name]());
    holder.replaceChildren(built[name]);
  };
  const names = Object.keys(panes);
  const fromHash = () => names.find(n => n.toLowerCase().replace(" ", "-") === location.hash.slice(1)) || "Summary";
  for (const name of names) {
    tabs.append(el("button", {
      onclick: () => { history.replaceState(null, "", `#${name.toLowerCase().replace(" ", "-")}`); show(name); },
    }, name));
  }
  show(fromHash());
}

function quiz(questions, at) {
  let answered = 0, correct = 0;
  const score = el("p", { class: "score" }, `0 of ${questions.length} answered`);
  const update = ok => {
    answered += 1;
    if (ok) correct += 1;
    score.textContent = `${correct} correct out of ${answered} answered (${questions.length} questions)`;
  };

  const cards = questions.map((q, i) => {
    const feedback = el("div", { class: "feedback", hidden: "" },
      el("p", {}, el("b", {}, "Answer: "), q.answer),
      el("p", {}, q.explanation),
      el("blockquote", {}, `Teacher said: "${q.evidence_quote}"`),
      jump(at(q.segment_ids), "Rewatch from"));
    const reveal = () => feedback.removeAttribute("hidden");

    let body;
    if (q.kind === "multiple_choice") {
      body = el("div", { class: "options" }, q.options.map(option => el("button", {
        onclick: ev => {
          const box = ev.target.parentElement;
          if (box.dataset.done) return;
          box.dataset.done = "1";
          const ok = option.trim().toLowerCase() === q.answer.trim().toLowerCase();
          ev.target.classList.add(ok ? "right" : "wrong");
          box.querySelectorAll("button").forEach(b => {
            if (b.textContent.trim().toLowerCase() === q.answer.trim().toLowerCase()) b.classList.add("right");
            b.disabled = true;
          });
          update(ok);
          reveal();
        },
      }, option)));
    } else {
      const input = el("input", { type: "text", placeholder: "Your answer" });
      body = el("div", { class: "short" }, input, el("button", {
        onclick: ev => {
          if (ev.target.disabled) return;
          ev.target.disabled = true;
          input.disabled = true;
          const ok = input.value.trim() !== "" && q.answer.toLowerCase().includes(input.value.trim().toLowerCase());
          update(ok);
          reveal();
        },
      }, "Check"));
    }
    return el("article", { class: "question" }, el("h3", {}, `${i + 1}. ${q.question}`), body, feedback);
  });
  return [score, ...cards];
}

// ------------------------------------------------------------------ ask
const TIME_IN_TEXT = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g;

function toSeconds(label) {
  return label.split(":").reduce((total, part) => total * 60 + Number(part), 0);
}

function withJumps(text) {
  // Turn every [mm:ss] into a button that plays the video from there.
  const nodes = [];
  let last = 0;
  for (const match of text.matchAll(TIME_IN_TEXT)) {
    nodes.push(text.slice(last, match.index));
    nodes.push(jump(toSeconds(match[1])));
    last = match.index + match[0].length;
  }
  nodes.push(text.slice(last));
  return nodes;
}

function studentId() {
  // ?student=<id> reopens a saved conversation, e.g. on another device.
  const fromUrl = new URLSearchParams(location.search).get("student");
  if (fromUrl && /^[A-Za-z0-9-]{8,64}$/.test(fromUrl)) localStorage.setItem("lna-student", fromUrl);
  let id = localStorage.getItem("lna-student");
  if (!id) {
    id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)).slice(0, 36);
    localStorage.setItem("lna-student", id);
  }
  return id;
}

function askPane() {
  const student = studentId();
  const log = el("div", { class: "chat" });
  const input = el("input", { type: "text", placeholder: "Ask anything about this lecture…", maxlength: "1000" });
  const send = el("button", { type: "submit" }, "Ask");
  const form = el("form", { class: "ask" }, input, send);

  const bubble = (who, children) => {
    const node = el("div", { class: `msg ${who}` }, children);
    log.append(node);
    node.scrollIntoView({ block: "nearest" });
    return node;
  };
  const addAnswer = (text, tools) => {
    const node = bubble("bot", withJumps(text));
    if (tools && tools.length) {
      const used = tools.map(t => t.tool.replace("_", " ") + (t.input ? ` (${t.input})` : "")).join(" · ");
      node.append(el("small", { class: "tools" }, `Looked up: ${used}`));
    }
  };

  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    input.disabled = send.disabled = true;
    bubble("me", question);
    const waiting = bubble("bot wait", "Looking through the lecture…");
    try {
      const res = await fetch(`/api/jobs/${jobId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ student, message: question }),
      });
      const data = await res.json();
      waiting.remove();
      if (!res.ok) bubble("bot error", data.error || "Something went wrong.");
      else addAnswer(data.answer, data.tools);
    } catch {
      waiting.remove();
      bubble("bot error", "Could not reach the server.");
    }
    input.disabled = send.disabled = false;
    input.focus();
  });

  fetch(`/api/jobs/${jobId}/chat?student=${encodeURIComponent(student)}`)
    .then(r => r.ok ? r.json() : { turns: [] })
    .then(({ turns }) => {
      if (!turns.length) bubble("bot", "Ask me anything about this lecture. I answer only from what the teacher said, with ▶ times so you can rewatch.");
      for (const t of turns) { bubble("me", t.question); addAnswer(t.answer); }
    });

  return [
    el("p", { class: "muted" }, "Answers come only from this lecture. Every ▶ time is checked against what the agent actually looked up."),
    log,
    form,
  ];
}

function quality(m) {
  const n = m.notes, q = m.quiz;
  const value = v => (v === undefined || v === null ? "–" : String(v));
  const tools = q.tool_calls ? Object.entries(q.tool_calls).map(([k, v]) => `${k} ${v}`).join(", ") : undefined;
  const rows = [
    ["Lecture length", fmt(m.lecture_seconds)],
    ["Time to make these notes", fmt(m.processing_seconds)],
    ["Transcript segments", n.segments_total],
    ["Segments covered by the notes", `${n.segments_cited} (${Math.round(100 * n.coverage)}%)`],
    ["Board frames sampled → kept", `${m.frames_sampled} → ${m.frames_kept}`],
    ["Questions the quiz agent submitted to check_question", q.checks],
    ["Failed the code check (quote not in the lecture, bad options)", q.code_failures],
    ["Failed the judge agent", q.judge_failures],
    ["Rejected as repeats of a saved question", q.duplicates],
    ["Near-match quotes replaced with the teacher's exact words", q.quotes_replaced_with_transcript],
    ["Times steering stopped the agent finishing early", q.completion_nudges],
    ["Quiz agent tool calls", tools],
    ["Verified questions in the quiz", q.accepted !== undefined ? `${q.accepted} of ${q.target}` : undefined],
  ];
  return [
    el("p", {}, "The quiz agent read the lecture with tools and submitted each question to check_question. Code confirmed the teacher's words are in the transcript, then a judge agent confirmed they prove the answer. Only questions that passed both are in the quiz."),
    el("table", { class: "quality" }, rows.map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, value(v))))),
  ];
}

poll();
