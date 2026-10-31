// Shared by every page: theme switching, toasts and scroll reveal.
// The theme is applied by a tiny inline script in <head> before paint (no
// flash); this file adds the toggle button and the helpers.

(function () {
  const root = document.documentElement;

  function setTheme(theme) {
    root.dataset.theme = theme;
    localStorage.setItem("lna-theme", theme);
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
  }

  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.addEventListener("click", () => setTheme(root.dataset.theme === "dark" ? "light" : "dark"));
      setTheme(root.dataset.theme || "light");
    }

    // Fade sections in as they scroll into view.
    const items = document.querySelectorAll(".reveal");
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver(entries => {
        for (const entry of entries) {
          if (entry.isIntersecting) { entry.target.classList.add("in"); io.unobserve(entry.target); }
        }
      }, { threshold: 0.12 });
      items.forEach(item => io.observe(item));
    } else {
      items.forEach(item => item.classList.add("in"));
    }
  });

  // Follow the system theme only until the user picks one.
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", e => {
    if (!localStorage.getItem("lna-theme")) root.dataset.theme = e.matches ? "dark" : "light";
  });

  let toastTimer = null;
  window.toast = function (message) {
    let node = document.getElementById("toast");
    if (!node) {
      node = document.createElement("div");
      node.id = "toast";
      node.className = "toast";
      node.setAttribute("role", "status");
      document.body.append(node);
    }
    node.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"/></svg>';
    node.append(document.createTextNode(message));
    node.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove("show"), 2200);
  };
})();
