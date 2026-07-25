"""Assembly of self-contained visual brief pages."""

from __future__ import annotations

from typing import Any

from .css import CSS
from .html import escape, render_timeline
from .validate import TRUST_LABELS

JS = """
(() => {
  const forms = document.querySelectorAll(".question-box");
  document.querySelectorAll(".ask-button").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const form = document.getElementById(button.dataset.target);
      if (!form) return;
      form.classList.toggle("open");
      if (form.classList.contains("open")) form.querySelector("textarea").focus();
    });
  });
  forms.forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const textarea = form.querySelector("textarea");
      const text = textarea.value.trim();
      const status = form.querySelector(".status");
      if (!text) return;
      status.textContent = "Sending…";
      try {
        const response = await fetch("ask", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({anchor_id: form.dataset.anchorId, text})
        });
        if (!response.ok) throw new Error("not accepted");
        const pending = document.createElement("p");
        pending.className = "pending";
        pending.textContent = "You asked: " + text + " — awaiting an answer";
        form.insertAdjacentElement("afterend", pending);
        textarea.value = "";
        form.classList.remove("open");
      } catch (error) {
        status.textContent = "Could not send. Is the local server running?";
      }
    });
  });
  document.querySelectorAll(".signal").forEach((button) => {
    button.addEventListener("click", async () => {
      const status = button.parentElement.nextElementSibling;
      try {
        const response = await fetch("signal", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            anchor_id: button.dataset.anchorId,
            signal: button.dataset.signal
          })
        });
        if (!response.ok) throw new Error("not accepted");
        status.textContent = "Feedback received: " + button.textContent;
      } catch (error) {
        status.textContent = "Could not send feedback.";
      }
    });
  });
  let version = null;
  async function checkVersion() {
    try {
      const response = await fetch("render-version", {cache: "no-store"});
      const current = await response.text();
      if (version !== null && current !== version) location.reload();
      version = current;
    } catch (error) {
      // The static document remains usable when the local server is absent.
    }
  }
  checkVersion();
  setInterval(checkVersion, 5000);
})();
"""


def render_page(data: dict[str, Any]) -> str:
    """Assemble a validated visual brief into one HTML document.

    Args:
        data: A validated visual brief document.

    Returns:
        A self-contained HTML document.
    """
    legend = "".join(
        f'<span class="chip {key}">{label}</span>'
        for key, label in TRUST_LABELS.items()
    )
    timeline = render_timeline(data["updates"])
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="icon" href="data:,">'
        f"<title>{escape(data['title'])}</title><style>{CSS}</style></head>"
        '<body><main class="page"><div class="eyebrow">Session briefing</div>'
        f'<h1>{escape(data["title"])}</h1>'
        f'<p class="deck">{escape(data["summary"])}</p>'
        f'<aside class="legend"><span class="legend-label">TRUST</span>{legend}'
        f'</aside><section aria-label="Session timeline">{timeline}</section>'
        f"</main><script>{JS}</script></body></html>"
    )
