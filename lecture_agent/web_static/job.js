// Job page: show progress while the agents run, then the notes beside the video.
// Everything that comes from a model is inserted with textContent, never as HTML.

const jobId = document.body.dataset.job;
const base = `/jobs/${jobId}`;
let player = null;

// ------------------------------------------------------------------ helpers
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const ICONS = {
  play: '<path d="M7 4v16l13-8Z" fill="currentColor" stroke="none"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  listen: '<path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"/><path d="M19 11a7 7 0 0 1-14 0M12 18v3"/>',
  look: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  notes: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  quiz: '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"/>',
  done: '<path d="m12 3 1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4Z"/>',
  summary: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  book: '<path d="M4 19.5V6a2 2 0 0 1 2-2h12v14H6a2 2 0 0 0-2 2Z"/><path d="M8 4v14"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m10.8 12.2 9.2-9.2M17 6l3 3M14 9l2 2"/>',
  board: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z"/>',
  shield: '<path d="M12 2 3 7v6c0 5 4 8 9 9 5-1 9-4 9-9V7Z"/><path d="m9 12 2 2 4-4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  bot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  send: '<path d="M4 12 20 4l-4 16-4-7Z"/><path d="m12 13 8-9"/>',
};

function icon(name, cls = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("class", cls);
  svg.innerHTML = ICONS[name] || "";  // static icon markup only, never model text
  return svg;
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
  if (window.matchMedia("(max-width: 980px)").matches) player.scrollIntoView({ behavior: "smooth", block: "center" });
}

function jump(seconds, label) {
  if (seconds === null || seconds === undefined) return null;
  return el("button", { class: "jump", title: "Play the video from here", onclick: () => seek(seconds) },
    icon("play"), label ? `${label} ${fmt(seconds)}` : fmt(seconds));
}

// ------------------------------------------------------------------ progress
const STEP_ORDER = ["listen", "look", "notes", "quiz", "done"];
const STEP_ICON = { listen: "listen", look: "look", notes: "notes", quiz: "quiz", done: "done" };

function currentStep(log) {
  let step = "listen";
  for (const line of log) {
    for (const name of STEP_ORDER) if (line.includes(`  ${name}:`)) step = name;
    if (line.includes("done in")) step = "done";
  }
  return step;
}

function renderSteps(step, log) {
  const now = STEP_ORDER.indexOf(step);
  document.querySelectorAll("#steps li").forEach(li => {
    const i = STEP_ORDER.indexOf(li.dataset.step);
    li.className = i < now ? "done" : i === now ? "active" : "";
    li.querySelector(".dot").replaceChildren(icon(i < now ? "check" : STEP_ICON[li.dataset.step]));
  });
  // Show the quiz agent's live count, e.g. "5 of 8 questions verified".
  const saved = [...log].reverse().find(l => l.includes("PASSED and saved"));
  const m = saved && saved.match(/\((\d+) of (\d+)\)/);
  if (m) document.getElementById("quiz-progress").textContent = `${m[1]} of ${m[2]} questions verified so far`;
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
  renderSteps(currentStep(job.log), job.log);
  const title = document.getElementById("status-title");
  const sub = document.getElementById("status-sub");

  if (job.status === "queued") {
    title.textContent = "Waiting in line…";
    sub.textContent = `Another lecture is being processed first (position ${job.queue_position}).`;
    setTimeout(poll, 3000);
  } else if (job.status === "running") {
    title.textContent = "Working on your lecture…";
    setTimeout(poll, 2000);
  } else if (job.status === "failed") {
    document.getElementById("progress-head").classList.add("failed");
    const stuck = document.querySelector("#steps li.active");
    if (stuck) {
      stuck.className = "failed";
      stuck.querySelector(".dot").replaceChildren(icon("x"));
    }
    title.textContent = "Something went wrong";
    sub.textContent = "Nothing more will be charged for this upload.";
    const err = document.getElementById("error");
    err.hidden = false;
    err.textContent = job.error || "The job failed.";
    document.querySelector("details.log").open = true;
  } else if (job.status === "done") {
    showResult(job);
  }
}

// ------------------------------------------------------------------ results
async function showResult(job) {
  const data = await (await fetch(`${base}/files/lecture.json`)).json();
  document.getElementById("progress-view").hidden = true;
  document.getElementById("result").hidden = false;

  const notes = data.notes;
  const m = data.metrics;
  const startOf = new Map(data.segments.map(s => [s.id, s.start]));
  const at = ids => {
    const starts = (ids || []).map(id => startOf.get(id)).filter(t => t !== undefined);
    return starts.length ? Math.min(...starts) : null;
  };

  document.title = `${notes.title} · Lecture Notes Agent`;
  document.getElementById("lecture-title").textContent = notes.title;
  document.getElementById("meta").append(
    el("span", { class: "chip" }, icon("clock"), `${fmt(m.lecture_seconds)} lecture`),
    el("span", { class: "chip" }, icon("book"), `${notes.sections.length} sections`),
    el("span", { class: "chip" }, icon("quiz"), `${data.quiz.length} verified questions`),
    el("span", { class: "chip" }, icon("shield"), `${Math.round(100 * m.notes.coverage)}% of the lecture covered`),
  );

  // Player and chapter list
  const attrs = { controls: true, preload: "metadata", src: `${base}/files/media` };
  if (job.kind !== "audio" && data.board.length) attrs.poster = `${base}/frames/${data.board[0].frame}`;
  player = el(job.kind === "audio" ? "audio" : "video", attrs);
  document.getElementById("media").append(player);

  const chapters = notes.sections
    .map((s, i) => ({ title: `${i + 1}. ${s.title}`, start: at(s.segment_ids) }))
    .filter(c => c.start !== null);
  const chapterButtons = chapters.map(c =>
    el("button", { class: "chapter", onclick: () => seek(c.start) }, el("span", { class: "t" }, fmt(c.start)), c.title));
  if (chapters.length) document.getElementById("chapters").append(el("h3", {}, "Chapters"), ...chapterButtons);
  player.addEventListener("timeupdate", () => {
    let current = -1;
    chapters.forEach((c, i) => { if (player.currentTime + 0.5 >= c.start) current = i; });
    chapterButtons.forEach((b, i) => b.classList.toggle("now", i === current));
  });

  const panes = {
    summary: { label: "Summary", icon: "summary", build: () => summaryPane(notes, at) },
    notes: { label: "Full notes", icon: "book", build: () => notesPane(notes, at) },
    terms: { label: "Key terms", icon: "key", count: notes.key_terms.length, build: () => termsPane(notes, at) },
    board: { label: "Board", icon: "board", count: data.board.filter(b => b.has_content).length, build: () => boardPane(data.board) },
    quiz: { label: "Quiz", icon: "quiz", count: data.quiz.length, build: () => quizPane(data.quiz, at) },
    ask: { label: "Ask", icon: "chat", build: () => askPane(notes) },
    quality: { label: "Quality", icon: "shield", build: () => qualityPane(m, data.quiz.length) },
  };

  const tabs = document.getElementById("tabs");
  const holder = document.getElementById("panes");
  const built = {};  // each pane is built once, so quiz answers and the chat survive tab switches
  const show = key => {
    tabs.querySelectorAll(".tab").forEach(b => {
      const on = b.dataset.key === key;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (!built[key]) built[key] = el("div", { class: "pane", role: "tabpanel" }, panes[key].build());
    holder.replaceChildren(built[key]);
  };
  for (const [key, pane] of Object.entries(panes)) {
    tabs.append(el("button", {
      class: "tab", role: "tab", "data-key": key,
      onclick: () => { history.replaceState(null, "", `${location.search}#${key}`); show(key); },
    }, icon(pane.icon), pane.label, pane.count ? el("span", { class: "count" }, pane.count) : null));
  }
  const fromHash = location.hash.slice(1).replace("full-notes", "notes").replace("key-terms", "terms");
  show(panes[fromHash] ? fromHash : "summary");
}

function summaryPane(notes, at) {
  return [
    el("div", { class: "summary-card" },
      el("div", { class: "label" }, icon("summary"), "In short"),
      el("p", {}, notes.summary)),
    el("h3", {}, "Key points"),
    el("ol", { class: "points" }, notes.key_points.map(k =>
      el("li", {}, el("span", {}, k.point, " ", jump(at(k.segment_ids)))))),
  ];
}

function notesPane(notes, at) {
  return notes.sections.map((s, i) =>
    el("article", { class: "section" },
      el("div", { class: "section-head" },
        el("span", { class: "num" }, i + 1),
        el("h3", {}, s.title),
        jump(at(s.segment_ids))),
      el("ul", {}, s.bullets.map(b => el("li", {}, b)))));
}

function termsPane(notes, at) {
  const out = [];
  if (notes.key_terms.length) {
    out.push(el("div", { class: "terms-grid" }, notes.key_terms.map(t =>
      el("div", { class: "term" },
        el("div", { class: "term-top" }, el("b", {}, t.term), jump(at(t.segment_ids))),
        el("p", {}, t.definition)))));
  }
  if (notes.worked_examples.length) {
    out.push(el("p", { class: "sub" }, "Worked examples"));
    for (const ex of notes.worked_examples) {
      out.push(el("div", { class: "example" },
        el("div", { class: "term-top" }, el("b", {}, ex.title), jump(at(ex.segment_ids))),
        el("ol", {}, ex.steps.map(step => el("li", {}, step)))));
    }
  }
  return out.length ? out : [el("p", { class: "empty" }, "The teacher did not define terms or work through examples.")];
}

function boardPane(board) {
  const boards = board.filter(b => b.has_content);
  if (!boards.length) return [el("p", { class: "empty" }, "Nothing was read from the board or slides.")];
  return [
    el("p", { class: "pane-intro" }, "Each new slide or board, read from the video. Click an image to watch that moment."),
    el("div", { class: "board-grid" }, boards.map(b =>
      el("figure", { class: "board" },
        el("img", { src: `${base}/frames/${b.frame}`, alt: `Board at ${fmt(b.time)}`, loading: "lazy", onclick: () => seek(b.time) }),
        el("figcaption", {},
          el("div", {}, jump(b.time)),
          el("pre", {}, b.text.replace(/\n\s*\n/g, "\n").trim()),
          b.formulas.filter(f => !b.text.includes(f)).map(f => el("code", {}, f)),
          b.diagram ? el("p", { class: "muted small" }, `Diagram: ${b.diagram}`) : null)))),
  ];
}

function quizPane(questions, at) {
  if (!questions.length) return [el("p", { class: "empty" }, "No questions passed checking for this lecture.")];
  let answered = 0, correct = 0;
  const fill = el("span");
  const label = el("b", {}, `0 of ${questions.length} answered`);
  const detail = el("span", { class: "muted small" }, "Every question is checked against the teacher's words.");
  const update = ok => {
    answered += 1;
    if (ok) correct += 1;
    label.textContent = `${correct} correct · ${answered} of ${questions.length} answered`;
    fill.style.width = `${(100 * correct) / questions.length}%`;
    if (answered === questions.length) {
      detail.textContent = correct === questions.length
        ? "All correct. Well done!"
        : "Use the ▶ links on the ones you missed to rewatch that part.";
    }
  };
  const scorebar = el("div", { class: "scorebar" }, el("div", { class: "row" }, label, detail), el("div", { class: "bar" }, fill));

  const cards = questions.map((q, i) => {
    const card = el("article", { class: "question" });
    const verdict = el("div", { class: "verdict" });
    const feedback = el("div", { class: "feedback", hidden: true },
      verdict,
      el("p", {}, q.explanation),
      el("blockquote", {}, el("span", { class: "who" }, "The teacher said"), `“${q.evidence_quote}”`),
      el("div", {}, jump(at(q.segment_ids), "Rewatch from")));
    const reveal = ok => {
      card.classList.add(ok ? "right" : "wrong");
      verdict.className = `verdict ${ok ? "ok" : "no"}`;
      verdict.replaceChildren(icon(ok ? "check" : "x"), ok ? "Correct" : `Not quite. The answer is: ${q.answer}`);
      feedback.hidden = false;
      update(ok);
    };

    let body;
    if (q.kind === "multiple_choice") {
      const normal = s => s.trim().toLowerCase();
      const buttons = q.options.map((option, n) => el("button", {
        class: "option",
        onclick: ev => {
          const chosen = ev.currentTarget;
          const ok = normal(option) === normal(q.answer);
          buttons.forEach(b => {
            b.disabled = true;
            if (normal(b.dataset.value) === normal(q.answer)) b.classList.add("right");
            else if (b !== chosen) b.classList.add("faded");
          });
          if (!ok) chosen.classList.add("wrong");
          reveal(ok);
        },
        "data-value": option,
      }, el("span", { class: "letter" }, "ABCDEFGH"[n]), el("span", {}, option),
         el("span", { class: "mark" }, icon(normal(option) === normal(q.answer) ? "check" : "x"))));
      body = el("div", { class: "options" }, buttons);
    } else {
      const input = el("input", { type: "text", placeholder: "Type your answer" });
      const check = el("button", { class: "btn btn-primary", type: "submit" }, "Check");
      body = el("form", {
        class: "short",
        onsubmit: ev => {
          ev.preventDefault();
          if (check.disabled) return;
          check.disabled = input.disabled = true;
          const given = input.value.trim().toLowerCase();
          reveal(given !== "" && (q.answer.toLowerCase().includes(given) || given.includes(q.answer.toLowerCase())));
        },
      }, input, check);
    }
    card.append(el("div", { class: "q-head" }, el("span", { class: "q-num" }, `Q${i + 1}`), el("h3", {}, q.question)), body, feedback);
    return card;
  });
  return [scorebar, ...cards];
}

// ------------------------------------------------------------------ ask
const TIME_IN_TEXT = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g;

function toSeconds(label) {
  return label.split(":").reduce((total, part) => total * 60 + Number(part), 0);
}

function withJumps(text) {
  // Every [mm:ss] becomes a button that plays the video from there.
  const nodes = [];
  let last = 0;
  for (const match of text.matchAll(TIME_IN_TEXT)) {
    nodes.push(text.slice(last, match.index).replace(/\s+$/, " "));
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

const TOOL_LABEL = {
  search_transcript: "searched the transcript",
  read_transcript: "read the transcript",
  list_sections: "checked the outline",
  read_board: "read the board",
};

function askPane(notes) {
  const student = studentId();
  const log = el("div", { class: "chat", "aria-live": "polite" });
  const input = el("input", { type: "text", placeholder: "Ask anything about this lecture…", maxlength: "1000", "aria-label": "Your question" });
  const send = el("button", { class: "btn btn-primary", type: "submit" }, icon("send"), "Ask");
  const form = el("form", { class: "ask" }, input, send);

  const message = (who, children) => {
    const node = el("div", { class: `msg ${who}` },
      el("span", { class: "avatar" }, icon(who.startsWith("me") ? "user" : "bot")),
      el("div", { class: "bubble" }, children));
    log.append(node);
    log.scrollTop = log.scrollHeight;
    return node;
  };
  const answer = (text, tools) => {
    const node = message("bot", withJumps(text));
    if (tools && tools.length) {
      const seen = [...new Set(tools.map(t => TOOL_LABEL[t.tool] || t.tool))];
      node.querySelector(".bubble").append(el("div", { class: "tools" }, seen.map(s => el("span", {}, s))));
    }
  };

  const ask = async question => {
    question = question.trim();
    if (!question) return;
    input.value = "";
    input.disabled = send.disabled = true;
    suggestions.hidden = true;
    message("me", question);
    const waiting = message("bot", el("span", { class: "typing" }, el("i"), el("i"), el("i")));
    try {
      const res = await fetch(`/api/jobs/${jobId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ student, message: question }),
      });
      const data = await res.json();
      waiting.remove();
      if (!res.ok) message("bot error", data.error || "Something went wrong.");
      else answer(data.answer, data.tools);
    } catch {
      waiting.remove();
      message("bot error", "Could not reach the server.");
    }
    input.disabled = send.disabled = false;
    input.focus();
  };
  form.addEventListener("submit", ev => { ev.preventDefault(); ask(input.value); });

  const ideas = ["Summarise this lecture in three sentences"];
  if (notes.key_terms[0]) ideas.push(`What does "${notes.key_terms[0].term}" mean?`);
  if (notes.sections[1]) ideas.push(`Explain "${notes.sections[1].title}" simply`);
  ideas.push("What should I revise first?");
  const suggestions = el("div", { class: "suggest" }, ideas.map(q => el("button", { type: "button", onclick: () => ask(q) }, q)));

  fetch(`/api/jobs/${jobId}/chat?student=${encodeURIComponent(student)}`)
    .then(r => (r.ok ? r.json() : { turns: [] }))
    .then(({ turns }) => {
      if (!turns.length) {
        message("bot", "Hi! Ask me anything about this lecture. I answer only from what the teacher said, and every ▶ time plays that moment.");
      }
      for (const t of turns) { message("me", t.question); answer(t.answer); }
      if (turns.length) suggestions.hidden = true;
    });

  return el("div", { class: "ask-wrap" },
    el("p", { class: "pane-intro" }, icon("shield"), " Answers come only from this lecture. Each ▶ time is checked against what the agent actually looked up."),
    log,
    el("div", { style: "display:grid;gap:10px" }, suggestions, form));
}

// ------------------------------------------------------------------ quality
function qualityPane(m, shown) {
  const n = m.notes, q = m.quiz;
  const value = v => (v === undefined || v === null ? "–" : String(v));
  const tools = q.tool_calls ? Object.entries(q.tool_calls).map(([k, v]) => `${k} × ${v}`).join(", ") : undefined;
  const stat = (v, k, good) => el("div", { class: `stat${good ? " good" : ""}` }, el("div", { class: "v" }, v), el("div", { class: "k" }, k));
  const target = q.target ?? shown;
  return [
    el("div", { class: "stats" },
      stat(`${Math.round(100 * n.coverage)}%`, "of the lecture cited by the notes", n.coverage >= 0.9),
      stat([String(shown), el("small", {}, ` / ${target}`)], "quiz questions verified", shown >= target),
      stat(value(q.checks), "questions the quiz agent submitted"),
      stat(value((q.code_failures ?? 0) + (q.judge_failures ?? 0) + (q.duplicates ?? 0)), "rejected and rewritten"),
      stat(fmt(m.processing_seconds), `to process a ${fmt(m.lecture_seconds)} lecture`),
    ),
    el("div", { class: "explain" },
      el("b", {}, "How every quiz question was checked"),
      el("div", { class: "flow" },
        el("span", { class: "chip" }, "Quiz agent reads the lecture"), el("span", { class: "arrow" }, "→"),
        el("span", { class: "chip" }, "check_question tool"), el("span", { class: "arrow" }, "→"),
        el("span", { class: "chip" }, "Quote is in the transcript? (code)"), el("span", { class: "arrow" }, "→"),
        el("span", { class: "chip" }, "Not a repeat? (code)"), el("span", { class: "arrow" }, "→"),
        el("span", { class: "chip" }, "Judge agent agrees"), el("span", { class: "arrow" }, "→"),
        el("span", { class: "chip" }, "✓ Saved"))),
    el("table", { class: "details" }, [
      ["Transcript lines", n.segments_total],
      ["Lines cited by the notes", n.segments_cited],
      ["Board frames sampled → kept", `${m.frames_sampled} → ${m.frames_kept}`],
      ["Failed the code check", q.code_failures],
      ["Failed the judge agent", q.judge_failures],
      ["Rejected as a repeat of a saved question", q.duplicates],
      ["Near-match quotes replaced with the teacher's exact words", q.quotes_replaced_with_transcript],
      ["Times steering kept the agent working", q.completion_nudges],
      ["Quiz agent tool calls", tools],
    ].map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, value(v))))),
  ];
}

poll();
