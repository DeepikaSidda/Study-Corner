// Lecture page: live progress while the agents run, then notes beside the video.
// Everything written by a model is inserted with textContent, never as HTML.

const jobId = document.body.dataset.job;
const base = `/jobs/${jobId}`;
let player = null;
const ui = {};  // show(key), rebuild(key) once results are on screen

// ================================================================== helpers
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
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
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  listen: '<path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"/><path d="M19 11a7 7 0 0 1-14 0M12 18v3"/>',
  look: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  notes: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  quiz: '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"/>',
  spark: '<path d="m12 3 1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4Z"/>',
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
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  reset: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  arrow: '<path d="m9 6 6 6-6 6"/>',
  upload: '<path d="M12 16V4"/><path d="m6 10 6-6 6 6"/><path d="M4 20h16"/>',
  cloud: '<path d="M7 18a5 5 0 1 1 1-9.9A6 6 0 0 1 19.5 11 3.5 3.5 0 0 1 18 18Z"/>',
};

function icon(name, cls = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", class: cls }))
    svg.setAttribute(k, v);
  svg.innerHTML = ICONS[name] || "";  // fixed icon markup from this file only
  return svg;
}

function fmt(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function seek(seconds) {
  if (!player) return;
  player.currentTime = seconds;
  player.play().catch(() => {});
  if (window.matchMedia("(max-width: 1020px)").matches) player.scrollIntoView({ behavior: "smooth", block: "center" });
}

function jump(seconds, label) {
  if (seconds === null || seconds === undefined) return null;
  return el("button", { class: "jump", type: "button", title: "Play the video from here", onclick: () => seek(seconds) },
    icon("play"), label ? `${label} ${fmt(seconds)}` : fmt(seconds));
}

const isTyping = target => target && (target.closest("input, textarea, select, [contenteditable]"));

// ================================================================== progress
const STEPS = ["listen", "look", "notes", "quiz", "done"];
const STEP_ICON = { listen: "listen", look: "look", notes: "notes", quiz: "quiz", done: "spark" };
let elapsedTimer = null;

function parseLog(lines) {
  // "20:19:46  listen: Amazon Transcribe" -> {time, text}
  return lines.map(line => {
    const m = line.match(/^(\d\d:\d\d:\d\d)\s{2}(.*)$/);
    return m ? { time: m[1], text: m[2] } : { time: "", text: line };
  });
}

const toSec = hms => { const [h, m, s] = hms.split(":").map(Number); return h * 3600 + m * 60 + s; };

function stepStarts(entries) {
  const starts = {};
  for (const e of entries) {
    for (const name of STEPS) if (!starts[name] && e.text.startsWith(`${name}:`)) starts[name] = e.time;
    if (!starts.done && e.text.startsWith("done in")) starts.done = e.time;
  }
  return starts;
}

function humanize(text) {
  const t = text.trim();
  let m;
  if ((m = t.match(/^uploaded (.+) \(([\d.]+ MB)\)/))) return ["upload", `Uploaded ${m[1]} (${m[2]})`];
  if ((m = t.match(/^lecture: .+\((\d+)s\)/))) return ["clock", `The lecture is ${fmt(Number(m[1]))} long`];
  if (t.startsWith("listen: Amazon Transcribe")) return ["listen", "Sending the audio to Amazon Transcribe"];
  if (t.startsWith("uploading audio")) return ["cloud", "Uploading the audio to a private S3 bucket"];
  if (/^transcription job .* started/.test(t)) return ["listen", "Transcription started"];
  if ((m = t.match(/^listen: (\d+) segments/))) return ["check", `Transcript ready: ${m[1]} timed lines`, "ok"];
  if (t.startsWith("listen: reusing") || t.startsWith("listen: using")) return ["check", "Using the saved transcript", "ok"];
  if ((m = t.match(/^look: (\d+) frames sampled, (\d+) kept/))) return ["look", `Found ${m[2]} slide changes in ${m[1]} frames`];
  if ((m = t.match(/^look: (\d+) frames had/))) return ["check", `Read the board on ${m[1]} slides`, "ok"];
  if (t.startsWith("look: skipped")) return ["info", "No video track, so the board was skipped"];
  if (t.startsWith("notes: writing")) return ["notes", "Writing the summary and notes"];
  if ((m = t.match(/^notes: (\d+) sections, coverage (\d+%)/))) return ["check", `Notes ready: ${m[1]} sections, ${m[2]} of the lecture covered`, "ok"];
  if ((m = t.match(/^quiz: agent building (\d+)/))) return ["quiz", `Quiz agent started, aiming for ${m[1]} questions`];
  if ((m = t.match(/check_question: PASSED and saved \((\d+) of (\d+)\).*<-\s*(.*)$/))) return ["check", `Verified ${m[1]}/${m[2]}: ${m[3]}`, "ok"];
  if ((m = t.match(/check_question: FAILED.*<-\s*(.*)$/))) return ["x", `Rejected, the agent will fix it: ${m[1]}`, "no"];
  if ((m = t.match(/^quiz: (\d+) verified questions/))) return ["check", `Quiz ready: ${m[1]} verified questions`, "ok"];
  if (t.startsWith("done in")) return ["spark", "All done", "ok"];
  if (t.startsWith("ERROR")) return ["x", t.replace(/^ERROR:\s*/, ""), "no"];
  return ["info", t];
}

function renderProgress(job) {
  const entries = parseLog(job.log);
  const starts = stepStarts(entries);
  let current = "listen";
  for (const name of STEPS) if (starts[name]) current = name;
  const now = STEPS.indexOf(current);
  const lastTime = entries.length ? entries[entries.length - 1].time : null;

  document.querySelectorAll("#steps li").forEach(li => {
    const name = li.dataset.step, i = STEPS.indexOf(name);
    li.className = i < now || (name === "done" && starts.done) ? "done" : i === now ? "active" : "";
    li.querySelector(".dot").replaceChildren(icon(li.className === "done" ? "check" : STEP_ICON[name]));
    const next = STEPS[i + 1];
    const when = li.querySelector(".when");
    if (starts[name] && name !== "done") {
      const end = starts[next] || (job.status === "running" ? null : lastTime);
      when.textContent = end ? fmt(toSec(end) - toSec(starts[name])) : "";
    } else when.textContent = "";
  });

  // Lecture length and live quiz count
  const lengthLine = entries.find(e => /^lecture: .*\((\d+)s\)/.test(e.text));
  if (lengthLine) document.getElementById("ps-length").textContent = fmt(Number(lengthLine.text.match(/\((\d+)s\)/)[1]));
  const saved = [...entries].reverse().find(e => e.text.includes("PASSED and saved"));
  const qm = saved && saved.text.match(/\((\d+) of (\d+)\)/);
  if (qm) {
    document.getElementById("ps-quiz").textContent = `${qm[1]} / ${qm[2]}`;
    document.getElementById("quiz-progress").textContent = `${qm[1]} of ${qm[2]} questions verified so far`;
  }

  // Overall progress: whole steps, plus the quiz's own fraction
  let progress = now / (STEPS.length - 1);
  if (current === "quiz" && qm) progress += (Number(qm[1]) / Number(qm[2])) / (STEPS.length - 1);
  document.getElementById("overall-bar").style.width = `${Math.min(100, Math.round(progress * 100))}%`;

  // Activity feed, newest first
  const feed = document.getElementById("activity");
  const items = entries.slice(-9).reverse().map(e => {
    const [ic, text, tone] = humanize(e.text);
    return el("li", { class: tone || "" }, el("time", {}, e.time), icon(ic), el("span", {}, text));
  });
  feed.replaceChildren(...items);
  document.getElementById("log").textContent = job.log.join("\n");

  // Elapsed clock
  const started = job.started || job.created;
  const tick = () => {
    const end = job.finished || Date.now() / 1000;
    document.getElementById("ps-elapsed").textContent = fmt(end - started);
  };
  tick();
  clearInterval(elapsedTimer);
  if (job.status === "running" || job.status === "queued") elapsedTimer = setInterval(tick, 1000);
}

async function poll() {
  let job;
  try {
    job = await (await fetch(`/api/jobs/${jobId}`)).json();
  } catch {
    setTimeout(poll, 4000);
    return;
  }
  document.getElementById("orb").replaceChildren(icon(job.status === "failed" ? "x" : "spark"));
  renderProgress(job);
  const title = document.getElementById("status-title");
  const sub = document.getElementById("status-sub");

  if (job.status === "queued") {
    title.textContent = "Waiting in line…";
    sub.textContent = `Another lecture is being processed first (position ${job.queue_position}). Yours starts right after.`;
    setTimeout(poll, 3000);
  } else if (job.status === "running") {
    title.textContent = "Making your notes…";
    setTimeout(poll, 2000);
  } else if (job.status === "failed") {
    clearInterval(elapsedTimer);
    document.getElementById("progress-head").classList.add("failed");
    document.getElementById("live").style.cssText = "background:var(--bad);animation:none";
    title.textContent = "Something went wrong";
    sub.textContent = "Nothing more will be charged for this upload. You can upload it again.";
    const stuck = document.querySelector("#steps li.active");
    if (stuck) { stuck.className = "failed"; stuck.querySelector(".dot").replaceChildren(icon("x")); }
    const err = document.getElementById("error");
    err.hidden = false;
    err.textContent = job.error || "The job failed.";
  } else if (job.status === "done") {
    clearInterval(elapsedTimer);
    showResult(job);
  }
}

// ================================================================== results
async function showResult(job) {
  document.getElementById("progress-view").hidden = true;
  document.getElementById("result").hidden = false;
  const data = await (await fetch(`${base}/files/lecture.json`)).json();

  const notes = data.notes;
  const m = data.metrics;
  const startOf = new Map(data.segments.map(s => [s.id, s.start]));
  const at = ids => {
    const starts = (ids || []).map(id => startOf.get(id)).filter(t => t !== undefined);
    return starts.length ? Math.min(...starts) : null;
  };

  document.title = `${notes.title} · Study Corner`;
  document.getElementById("nav-title").textContent = notes.title;
  document.getElementById("lecture-title").textContent = notes.title;
  document.getElementById("meta").append(
    el("span", { class: "chip" }, icon("clock"), `${fmt(m.lecture_seconds)} lecture`),
    el("span", { class: "chip" }, icon("book"), `${notes.sections.length} sections`),
    el("span", { class: "chip" }, icon("quiz"), `${data.quiz.length} verified questions`),
    el("span", { class: "chip" }, icon("shield"), `${Math.round(100 * m.notes.coverage)}% of the lecture covered`),
  );

  // ---- player, timeline and chapters
  const attrs = { controls: true, preload: "metadata", src: `${base}/files/media`, playsinline: true };
  if (job.kind !== "audio" && data.board.length) attrs.poster = `${base}/frames/${data.board[0].frame}`;
  player = el(job.kind === "audio" ? "audio" : "video", attrs);
  document.getElementById("media").append(player);

  const chapters = notes.sections
    .map((s, i) => ({ n: i + 1, title: s.title, start: at(s.segment_ids) }))
    .filter(c => c.start !== null)
    .sort((a, b) => a.start - b.start);
  const total = m.lecture_seconds || (chapters.length ? chapters[chapters.length - 1].start + 30 : 60);
  chapters.forEach((c, i) => { c.end = i + 1 < chapters.length ? chapters[i + 1].start : total; });

  const segs = chapters.map(c => el("button", {
    class: "tl-seg", type: "button", style: `flex-grow:${Math.max(1, c.end - c.start)}`,
    "aria-label": `Chapter ${c.n}: ${c.title}, ${fmt(c.start)}`, onclick: () => seek(c.start),
  }, el("span", { class: "tl-fill" }), el("span", { class: "tl-tip" }, `${fmt(c.start)} · ${c.title}`)));
  document.getElementById("timeline").append(...segs);

  const rows = chapters.map(c => el("button", { class: "chapter", type: "button", onclick: () => seek(c.start) },
    el("span", { class: "t" }, fmt(c.start)), el("span", {}, `${c.n}. ${c.title}`), icon("play", "icon go")));
  if (chapters.length) {
    document.getElementById("chapters").append(
      el("h3", {}, el("span", {}, "Chapters"), el("span", {}, `${chapters.length}`)), ...rows);
  }
  player.addEventListener("timeupdate", () => {
    const t = player.currentTime;
    chapters.forEach((c, i) => {
      const on = t + 0.4 >= c.start && t < c.end;
      rows[i].classList.toggle("now", on);
      segs[i].classList.toggle("now", on);
      const frac = t >= c.end ? 1 : t <= c.start ? 0 : (t - c.start) / (c.end - c.start);
      segs[i].firstChild.style.width = `${frac * 100}%`;
    });
  });

  // ---- tabs and panes
  const panes = {
    summary: { label: "Summary", icon: "summary", build: () => summaryPane(notes, at, data) },
    notes: { label: "Full notes", icon: "book", build: () => notesPane(notes, at) },
    terms: { label: "Key terms", icon: "key", count: notes.key_terms.length + notes.worked_examples.length, build: () => termsPane(notes, at) },
    board: { label: "Board", icon: "board", count: data.board.filter(b => b.has_content).length, build: () => boardPane(data.board) },
    quiz: { label: "Quiz", icon: "quiz", count: data.quiz.length, build: () => quizPane(data.quiz, at) },
    ask: { label: "Ask", icon: "chat", build: () => askPane(notes) },
    quality: { label: "Quality", icon: "shield", build: () => qualityPane(m, data.quiz.length) },
  };
  const keys = Object.keys(panes);
  const tabs = document.getElementById("tabs");
  const holder = document.getElementById("panes");
  const built = {};  // each pane is built once, so quiz answers and the chat survive tab switches

  const show = (key, focusTab = false) => {
    tabs.querySelectorAll(".tab").forEach(b => {
      const on = b.dataset.key === key;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
      if (on && focusTab) b.focus();
      if (on) b.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    if (!built[key]) built[key] = el("div", { class: "pane", role: "tabpanel" }, panes[key].build());
    holder.replaceChildren(built[key]);
    history.replaceState(null, "", `${location.search}#${key}`);
  };
  ui.show = show;
  ui.rebuild = key => { delete built[key]; show(key); };

  for (const [key, pane] of Object.entries(panes)) {
    tabs.append(el("button", { class: "tab", role: "tab", type: "button", "data-key": key, onclick: () => show(key) },
      icon(pane.icon), pane.label, pane.count ? el("span", { class: "count" }, pane.count) : null));
  }
  tabs.addEventListener("keydown", ev => {
    const i = keys.indexOf(document.activeElement?.dataset?.key);
    if (i < 0) return;
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: keys.length - 1 }[ev.key];
    if (to === undefined) return;
    ev.preventDefault();
    show(keys[(to + keys.length) % keys.length], true);
  });
  document.addEventListener("keydown", ev => {
    if (ev.ctrlKey || ev.metaKey || ev.altKey || isTyping(ev.target)) return;
    const n = Number(ev.key);
    if (n >= 1 && n <= keys.length) { show(keys[n - 1]); return; }
    if (ev.key === "k" || ev.key === "K") { ev.preventDefault(); player.paused ? player.play().catch(() => {}) : player.pause(); }
    if (ev.key === "/") { ev.preventDefault(); show("ask"); setTimeout(() => document.querySelector("form.ask textarea")?.focus(), 50); }
  });

  const fromHash = location.hash.slice(1).replace("full-notes", "notes").replace("key-terms", "terms");
  show(panes[fromHash] ? fromHash : "summary");
}

// ------------------------------------------------------------------ summary
function summaryPane(notes, at, data) {
  const quick = (ic, title, text, key) => el("button", { type: "button", onclick: () => ui.show(key) }, el("b", {}, icon(ic), title), el("span", {}, text));
  return [
    el("div", { class: "summary-card" },
      el("div", { class: "label" }, icon("spark"), "In short"),
      el("p", {}, notes.summary)),
    el("div", { class: "quick" },
      quick("book", "Read the full notes", `${notes.sections.length} sections in lecture order`, "notes"),
      quick("quiz", "Test yourself", `${data.quiz.length} questions, all checked`, "quiz"),
      quick("chat", "Ask the lecture", "Answers only from what was said", "ask")),
    el("div", { class: "pane-title" }, el("h3", {}, "Key points"), el("span", { class: "muted small" }, `${notes.key_points.length} points`)),
    el("ol", { class: "points" }, notes.key_points.map(k => el("li", {}, el("span", {}, k.point, " ", jump(at(k.segment_ids)))))),
  ];
}

// ------------------------------------------------------------------ notes
function notesPane(notes, at) {
  return notes.sections.map((s, i) =>
    el("article", { class: "note-section" },
      el("span", { class: "num" }, i + 1),
      el("div", {},
        el("div", { class: "head" }, el("h3", {}, s.title), jump(at(s.segment_ids))),
        el("ul", {}, s.bullets.map(b => el("li", {}, b))))));
}

// ------------------------------------------------------------------ terms
function termsPane(notes, at) {
  const out = [];
  if (notes.key_terms.length) {
    out.push(el("div", { class: "terms-grid" }, notes.key_terms.map(t =>
      el("div", { class: "term" },
        el("div", { class: "term-top" }, el("b", {}, t.term), jump(at(t.segment_ids))),
        el("p", {}, t.definition)))));
  }
  if (notes.worked_examples.length) {
    out.push(el("div", { class: "pane-title" }, el("h3", {}, "Worked examples")));
    for (const ex of notes.worked_examples) {
      out.push(el("div", { class: "example" },
        el("div", { class: "term-top" }, el("b", {}, ex.title), jump(at(ex.segment_ids))),
        el("ol", {}, ex.steps.map(step => el("li", {}, el("span", {}, step))))));
    }
  }
  return out.length ? out : [el("div", { class: "empty" }, icon("key"), "The teacher did not define terms or work through examples.")];
}

// ------------------------------------------------------------------ board
function boardPane(board) {
  const boards = board.filter(b => b.has_content);
  if (!boards.length) return [el("div", { class: "empty" }, icon("board"), "Nothing was read from the board or slides.")];
  return [
    el("p", { class: "pane-intro" }, icon("board"), "Every new slide or board, read from the video. Click a picture to watch that moment."),
    el("div", { class: "board-grid" }, boards.map(b =>
      el("figure", { class: "board" },
        el("button", { class: "shot", type: "button", onclick: () => seek(b.time), "aria-label": `Play from ${fmt(b.time)}` },
          el("img", { src: `${base}/frames/${b.frame}`, alt: `Board at ${fmt(b.time)}`, loading: "lazy" }),
          el("span", { class: "ov" }, el("span", {}, icon("play"), `Play from ${fmt(b.time)}`))),
        el("figcaption", {},
          el("div", {}, jump(b.time)),
          el("pre", {}, b.text.replace(/\n\s*\n/g, "\n").trim()),
          b.formulas.filter(f => !b.text.includes(f)).map(f => el("code", {}, f)),
          b.diagram ? el("p", { class: "muted small", style: "margin:0" }, `Diagram: ${b.diagram}`) : null)))),
  ];
}

// ------------------------------------------------------------------ quiz
function confetti() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const colors = ["#14b8a6", "#0ea5e9", "#6366f1", "#f59e0b", "#22c55e", "#ec4899"];
  const layer = el("div", { class: "confetti" });
  for (let i = 0; i < 110; i++) {
    layer.append(el("i", { style: `left:${Math.random() * 100}vw;background:${colors[i % colors.length]};animation-duration:${1.8 + Math.random() * 1.6}s;animation-delay:${Math.random() * .4}s;transform:rotate(${Math.random() * 360}deg)` }));
  }
  document.body.append(layer);
  setTimeout(() => layer.remove(), 4000);
}

function quizPane(questions, at) {
  if (!questions.length) return [el("div", { class: "empty" }, icon("quiz"), "No questions passed checking for this lecture.")];
  let answered = 0, correct = 0;
  const C = 2 * Math.PI * 26;
  const val = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  const ringSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  ringSvg.setAttribute("viewBox", "0 0 64 64");
  ringSvg.innerHTML = '<circle class="track" cx="32" cy="32" r="26"/>';
  for (const [k, v] of Object.entries({ class: "val", cx: 32, cy: 32, r: 26, "stroke-dasharray": C, "stroke-dashoffset": C })) val.setAttribute(k, v);
  ringSvg.append(val);
  const pct = el("b", {}, "0%");
  const title = el("h3", {}, "Test yourself");
  const sub = el("p", {}, `${questions.length} questions · every one checked against the teacher's words`);
  const update = ok => {
    answered += 1;
    if (ok) correct += 1;
    val.setAttribute("stroke-dashoffset", String(C * (1 - correct / questions.length)));
    pct.textContent = `${Math.round((100 * correct) / questions.length)}%`;
    title.textContent = `${correct} correct · ${answered} of ${questions.length} answered`;
    if (answered === questions.length) {
      const score = correct / questions.length;
      sub.textContent = score === 1 ? "Perfect score. You really know this lecture!"
        : score >= 0.7 ? "Great work. Use ▶ on the ones you missed to rewatch them."
        : "Rewatch the parts you missed with the ▶ links, then try again.";
      if (score >= 0.7) { confetti(); window.toast?.(score === 1 ? "Perfect score!" : "Nice work!"); }
    }
  };
  const head = el("div", { class: "quiz-head" },
    el("div", { class: "ring" }, ringSvg, pct),
    el("div", {}, title, sub),
    el("button", { class: "btn btn-sm", type: "button", onclick: () => ui.rebuild("quiz") }, icon("reset"), "Start over"));

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
      verdict.replaceChildren(icon(ok ? "check" : "x"), ok ? "Correct!" : `Not quite. The answer is: ${q.answer}`);
      feedback.hidden = false;
      update(ok);
    };

    let body;
    if (q.kind === "multiple_choice") {
      const norm = s => s.trim().toLowerCase();
      const buttons = q.options.map((option, n) => el("button", {
        class: "option", type: "button", "data-value": option,
        onclick: ev => {
          const chosen = ev.currentTarget;
          const ok = norm(option) === norm(q.answer);
          buttons.forEach(b => {
            b.disabled = true;
            if (norm(b.dataset.value) === norm(q.answer)) b.classList.add("right");
            else if (b !== chosen) b.classList.add("faded");
          });
          if (!ok) chosen.classList.add("wrong");
          reveal(ok);
        },
      }, el("span", { class: "letter" }, "ABCDEFGH"[n]), el("span", {}, option),
         el("span", { class: "mark" }, icon(norm(option) === norm(q.answer) ? "check" : "x"))));
      body = el("div", { class: "options" }, buttons);
    } else {
      const input = el("input", { type: "text", placeholder: "Type your answer", "aria-label": "Your answer" });
      const check = el("button", { class: "btn btn-primary", type: "submit" }, "Check");
      body = el("form", {
        class: "short",
        onsubmit: ev => {
          ev.preventDefault();
          if (check.disabled) return;
          check.disabled = input.disabled = true;
          const given = input.value.trim().toLowerCase();
          const answer = q.answer.toLowerCase();
          reveal(given !== "" && (answer.includes(given) || given.includes(answer)));
        },
      }, input, check);
    }
    card.append(el("div", { class: "q-head" }, el("span", { class: "q-num" }, `Q${i + 1}`), el("h3", {}, q.question)), body, feedback);
    return card;
  });
  return [head, ...cards];
}

// ------------------------------------------------------------------ ask
const TIME_IN_TEXT = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g;
const toSeconds = label => label.split(":").reduce((t, p) => t * 60 + Number(p), 0);

function withJumps(text) {
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

const TOOL_LABEL = {
  search_transcript: "Searched the transcript",
  read_transcript: "Read the transcript",
  list_sections: "Checked the outline",
  read_board: "Read the board",
};

function askPane(notes) {
  const student = studentId();
  const log = el("div", { class: "chat", "aria-live": "polite" });
  const input = el("textarea", { rows: "1", placeholder: "Ask anything about this lecture…", maxlength: "1000", "aria-label": "Your question" });
  const send = el("button", { class: "btn btn-primary", type: "submit" }, icon("send"), el("span", { class: "hide-sm" }, "Ask"));
  const form = el("form", { class: "ask" }, input, send);

  const grow = () => { input.style.height = "auto"; input.style.height = `${Math.min(160, input.scrollHeight)}px`; };
  input.addEventListener("input", grow);
  input.addEventListener("keydown", ev => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); form.requestSubmit(); }
  });

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
    const used = [...new Set((tools || []).map(t => TOOL_LABEL[t.tool] || t.tool))];
    node.querySelector(".bubble").append(el("div", { class: "msg-foot" },
      used.map(u => el("span", { class: "src" }, u)),
      el("button", { class: "copy", type: "button", title: "Copy answer",
        onclick: () => navigator.clipboard.writeText(text).then(() => window.toast?.("Answer copied"), () => {}) }, icon("copy"))));
  };

  const ask = async question => {
    question = question.trim();
    if (!question || send.disabled) return;
    input.value = "";
    grow();
    input.disabled = send.disabled = true;
    suggestions.hidden = true;
    message("me", question);
    const waiting = message("bot", el("span", { class: "typing", "aria-label": "Thinking" }, el("i"), el("i"), el("i")));
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
  const suggestions = el("div", { class: "suggest" },
    ideas.map(q => el("button", { type: "button", onclick: () => ask(q) }, icon("spark"), q)));

  fetch(`/api/jobs/${jobId}/chat?student=${encodeURIComponent(student)}`)
    .then(r => (r.ok ? r.json() : { turns: [] }))
    .then(({ turns }) => {
      if (!turns.length) {
        message("bot", "Hi! I've studied this lecture. Ask me anything and I'll answer only from what the teacher said. Every ▶ time plays that exact moment.");
      }
      for (const t of turns) { message("me", t.question); answer(t.answer); }
      if (turns.length) suggestions.hidden = true;
    });

  return el("div", { class: "ask-wrap" },
    el("p", { class: "pane-intro" }, icon("shield"), "Answers come only from this lecture. Every ▶ time is checked against what the agent actually looked up."),
    log, suggestions, form,
    el("div", { class: "ask-hint" }, "Enter to send · Shift + Enter for a new line"));
}

// ------------------------------------------------------------------ quality
function qualityPane(m, shown) {
  const n = m.notes, q = m.quiz;
  const value = v => (v === undefined || v === null ? "–" : String(v));
  const tools = q.tool_calls ? Object.entries(q.tool_calls).map(([k, v]) => `${k} × ${v}`).join(", ") : undefined;
  const stat = (v, k, good) => el("div", { class: `stat${good ? " good" : ""}` }, el("div", { class: "v" }, v), el("div", { class: "k" }, k));
  const target = q.target ?? shown;
  const rejected = (q.code_failures ?? 0) + (q.judge_failures ?? 0) + (q.duplicates ?? 0);
  const step = (text, end) => el("span", { class: `chip${end ? " end" : ""}` }, text);
  const arrow = () => el("span", { class: "arrow" }, "→");
  return [
    el("p", { class: "pane-intro" }, icon("shield"), "How much of the lecture the notes cover, and how hard the quiz was checked."),
    el("div", { class: "stats" },
      stat(`${Math.round(100 * n.coverage)}%`, "of the lecture cited by the notes", n.coverage >= 0.9),
      stat([String(shown), el("small", {}, ` / ${target}`)], "quiz questions verified", shown >= target),
      stat(value(q.checks), "questions the quiz agent submitted"),
      stat(String(rejected), "rejected and rewritten"),
      stat(fmt(m.processing_seconds), `to process a ${fmt(m.lecture_seconds)} lecture`),
    ),
    el("div", { class: "flowbox" },
      el("b", {}, "How every quiz question was checked"),
      el("div", { class: "flow" },
        step("Quiz agent reads the lecture"), arrow(),
        step("check_question tool"), arrow(),
        step("Quote is in the transcript (code)"), arrow(),
        step("Not a repeat (code)"), arrow(),
        step("Judge agent agrees"), arrow(),
        step("✓ Saved", true))),
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
