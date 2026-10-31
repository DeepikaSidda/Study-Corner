// Show-notes page: contents list, reading progress, scroll-spy, copy and print.
(function () {
  "use strict";
  const doc = document.getElementById("doc");
  const toc = document.getElementById("toc");
  if (!doc) return;

  // Heading text without the time chip (" 01:23").
  const label = h => {
    const copy = h.cloneNode(true);
    copy.querySelectorAll(".t").forEach(t => t.remove());
    return copy.textContent.trim();
  };

  const targets = [...doc.querySelectorAll("#summary, h2[id], h3[id]")];
  const links = new Map();
  if (toc) {
    for (const node of targets) {
      const a = document.createElement("a");
      a.href = "#" + node.id;
      a.textContent = node.id === "summary" ? "In short" : label(node);
      if (node.tagName === "H3") a.className = "sub";
      toc.appendChild(a);
      links.set(node.id, a);
    }
  }

  // Reading time: about 200 words a minute.
  const words = (doc.innerText || "").split(/\s+/).filter(Boolean).length;
  const readTime = document.getElementById("read-time");
  if (readTime && words) readTime.textContent = `${words.toLocaleString()} words · about ${Math.max(1, Math.round(words / 200))} min read`;

  const bar = document.getElementById("rp");
  let current = null;
  const onScroll = () => {
    const max = document.documentElement.scrollHeight - innerHeight;
    if (bar) bar.style.width = (max > 0 ? Math.min(100, (scrollY / max) * 100) : 100) + "%";

    let active = targets[0];
    for (const node of targets) {
      if (node.getBoundingClientRect().top < 140) active = node; else break;
    }
    if (active && active.id !== current) {
      if (current && links.get(current)) links.get(current).classList.remove("on");
      current = active.id;
      const link = links.get(current);
      if (link) {
        link.classList.add("on");
        const box = toc.getBoundingClientRect(), r = link.getBoundingClientRect();
        if (r.top < box.top || r.bottom > box.bottom) link.scrollIntoView({ block: "nearest" });
      }
    }
  };
  addEventListener("scroll", onScroll, { passive: true });
  addEventListener("resize", onScroll);
  onScroll();

  const say = msg => (window.toast ? window.toast(msg) : null);
  const copy = document.getElementById("copy");
  if (copy) copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(doc.innerText);
      say("Notes copied to the clipboard");
    } catch {
      say("Select the text and press Ctrl+C to copy");
    }
  });
  const print = document.getElementById("print");
  if (print) print.addEventListener("click", () => window.print());
})();
