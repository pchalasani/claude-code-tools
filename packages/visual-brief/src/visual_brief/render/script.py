"""Browser enhancement for keyboard control and reverse-channel forms."""

JS = r"""
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const help = $("#key-help");
  const searchPanel = $("#search-panel");
  const searchInput = $("#page-search");
  const matchCount = $("#match-count");
  let focusBeforeOverlay = null;
  let awaitingIndex = -1;

  function isTyping(target) {
    return target instanceof Element && (
      target.matches("textarea,input,[contenteditable]") ||
      target.closest("[contenteditable]") !== null
    );
  }
  function isInteractive(target) {
    return target instanceof Element && (
      target.closest("button,a,select,summary") !== null
    );
  }
  function visible(elements) {
    return elements.filter((element) => !element.closest("[hidden]"));
  }

  function nav(kind) {
    return visible($$(`[data-nav-kind="${kind}"]`));
  }

  function openAncestors(element) {
    let parent = element.parentElement;
    while (parent) {
      if (parent instanceof HTMLDetailsElement) parent.open = true;
      parent = parent.parentElement;
    }
  }

  function focusElement(element) {
    if (!element) return;
    openAncestors(element);
    $$(".nav-focus").forEach((node) => node.classList.remove("nav-focus"));
    element.classList.add("nav-focus");
    element.focus({preventScroll: true});
    element.scrollIntoView({block: "nearest"});
  }

  function move(kind, delta) {
    const elements = nav(kind);
    if (!elements.length) return;
    const focused = document.activeElement;
    let relative = focused;
    if (focused instanceof HTMLElement) {
      const shell = focused.closest(`.${kind}-shell`);
      const selector = `:scope > details > summary[data-nav-kind="${kind}"]`;
      const summary = shell && $(selector, shell);
      if (summary) relative = summary;
    }
    const current = elements.indexOf(relative);
    const start = delta > 0 ? 0 : elements.length - 1;
    const next = current < 0
      ? start
      : Math.max(0, Math.min(elements.length - 1, current + delta));
    focusElement(elements[next]);
  }

  function toggleFocused() {
    const focused = document.activeElement;
    if (!(focused instanceof HTMLElement)) return;
    const details = focused.closest("details");
    if (details && details.querySelector(":scope > summary") === focused) {
      details.open = !details.open;
    }
  }

  function askFocused() {
    const focused = document.activeElement;
    if (!(focused instanceof HTMLElement)) return;
    if (focused.dataset.navKind === "thread") {
      const form = $("form.reply-box", focused.parentElement);
      const textarea = form && $("textarea", form);
      if (textarea) textarea.focus();
      return;
    }
    const focusId = focused.dataset.focusId;
    const shell = focusId && document.getElementById(focusId);
    const button = shell && $(":scope > .ask-button", shell);
    const form = button && document.getElementById(button.dataset.target);
    const textarea = form && $("textarea", form);
    if (!button || !form || !textarea) return;
    form.classList.add("open");
    button.setAttribute("aria-expanded", "true");
    textarea.focus();
  }

  function nextAwaiting() {
    const threads = $$("details.thread[data-awaiting]");
    if (!threads.length) return;
    awaitingIndex = (awaitingIndex + 1) % threads.length;
    const summary = $(":scope > summary", threads[awaitingIndex]);
    threads[awaitingIndex].open = true;
    focusElement(summary);
  }

  function openSearch() {
    if (!searchPanel.hidden) {
      searchInput.focus();
      return;
    }
    focusBeforeOverlay = document.activeElement;
    searchPanel.hidden = false;
    searchInput.focus();
  }

  function closeSearch() {
    searchInput.value = "";
    filterItems("");
    searchPanel.hidden = true;
    if (focusBeforeOverlay instanceof HTMLElement) {
      focusElement(focusBeforeOverlay);
    }
  }

  function filterItems(query) {
    const needle = query.toLocaleLowerCase();
    let matches = 0;
    $$(".item-shell").forEach((item) => {
      const matched = !needle ||
        item.textContent.toLocaleLowerCase().includes(needle);
      item.hidden = !matched;
      if (matched) matches += 1;
    });
    matchCount.textContent = `${matches} ${matches === 1 ? "match" : "matches"}`;
  }

  function openHelp() {
    focusBeforeOverlay = document.activeElement;
    if (typeof help.showModal === "function") help.showModal();
    else help.setAttribute("open", "");
    $("#close-help").focus();
  }

  function closeHelp() {
    if (typeof help.close === "function") help.close();
    else help.removeAttribute("open");
    if (focusBeforeOverlay instanceof HTMLElement) {
      focusElement(focusBeforeOverlay);
    }
  }

  function trapHelpFocus(event) {
    if (event.key !== "Tab") return;
    const elements = visible($$(
      "button,[href],input,select,textarea,[tabindex]:not([tabindex='-1'])",
      help,
    ));
    if (!elements.length) return;
    const first = elements[0];
    const last = elements[elements.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function jump(edge) {
    const elements = visible($$('[data-nav-kind="lane"],[data-nav-kind="item"]'));
    if (!elements.length) return;
    focusElement(edge === "top" ? elements[0] : elements[elements.length - 1]);
  }

  function runAction(action) {
    const actions = {
      "next-item": () => move("item", 1),
      "previous-item": () => move("item", -1),
      "next-lane": () => move("lane", 1),
      "previous-lane": () => move("lane", -1),
      "next-awaiting": nextAwaiting,
      "search": openSearch,
      "top": () => jump("top"),
      "bottom": () => jump("bottom"),
      "help": openHelp,
    };
    if (actions[action]) actions[action]();
  }
  function handleKey(event) {
    if (help.open) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeHelp();
      }
      return;
    }
    if (isTyping(event.target)) {
      if (event.key === "Escape") {
        event.preventDefault();
        if (event.target === searchInput) closeSearch();
        else event.target.blur();
      }
      return;
    }
    if (event.key === "Escape" && !searchPanel.hidden) {
      event.preventDefault();
      closeSearch();
      return;
    }
    if (event.key === " " && isInteractive(event.target)
        && !event.target.matches("summary")) {
      return;
    }
    const actions = {
      j: () => move("item", 1),
      k: () => move("item", -1),
      J: () => move("lane", 1),
      K: () => move("lane", -1),
      " ": toggleFocused,
      a: askFocused,
      n: nextAwaiting,
      "/": openSearch,
      g: () => jump("top"),
      G: () => jump("bottom"),
      "?": openHelp,
    };
    const action = actions[event.key];
    if (!action) return;
    event.preventDefault();
    action();
  }

  function setupDisclosures() {
    $$("details").forEach((details, index) => {
      const summary = $(":scope > summary", details);
      if (!summary) return;
      if (!summary.getAttribute("aria-controls")) {
        let body = summary.nextElementSibling;
        if (!body) return;
        if (!body.id) body.id = `disclosure-body-${index}`;
        summary.setAttribute("aria-controls", body.id);
      }
      const sync = () => {
        summary.setAttribute("aria-expanded", String(details.open));
      };
      sync();
      details.addEventListener("toggle", sync);
    });
  }

  function setupForms() {
    $$(".ask-button").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const form = document.getElementById(button.dataset.target);
        if (!form) return;
        form.classList.toggle("open");
        button.setAttribute(
          "aria-expanded",
          String(form.classList.contains("open")),
        );
        if (form.classList.contains("open")) $("textarea", form).focus();
      });
    });
    $$(".question-box").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const textarea = $("textarea", form);
        const text = textarea.value.trim();
        const status = $(".status", form);
        if (!text) return;
        status.textContent = "Sending…";
        const payload = {anchor_id: form.dataset.anchorId, text};
        if (form.dataset.parentId) payload.parent_id = form.dataset.parentId;
        try {
          const response = await fetch("ask", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload),
          });
          if (!response.ok) throw new Error("not accepted");
          const pending = document.createElement("p");
          pending.className = "pending";
          pending.textContent = `You asked: ${text} — awaiting an answer`;
          form.insertAdjacentElement("beforebegin", pending);
          textarea.value = "";
          status.textContent = "";
          if (!form.classList.contains("reply-box")) {
            form.classList.remove("open");
            const button = document.querySelector(
              `.ask-button[data-target="${CSS.escape(form.id)}"]`,
            );
            if (button) button.setAttribute("aria-expanded", "false");
          }
        } catch (error) {
          status.textContent = "Could not send. Is the local server running?";
        }
      });
    });
  }

  function setupSignals() {
    $$(".signal").forEach((button) => {
      button.addEventListener("click", async () => {
        const status = button.parentElement.nextElementSibling;
        try {
          const response = await fetch("signal", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              anchor_id: button.dataset.anchorId,
              signal: button.dataset.signal,
            }),
          });
          if (!response.ok) throw new Error("not accepted");
          status.textContent = `Feedback received: ${button.textContent}`;
        } catch (error) {
          status.textContent = "Could not send feedback.";
        }
      });
    });
  }

  function focusFromSavedId() {
    let saved = null;
    try {
      saved = sessionStorage.getItem("visual-brief-focus");
      sessionStorage.removeItem("visual-brief-focus");
    } catch (error) {
      // Storage may be disabled; keyboard navigation still works.
    }
    while (saved) {
      const match = $$("[data-focus-id]").find(
        (element) => element.dataset.focusId === saved,
      );
      if (match) {
        focusElement(match);
        return;
      }
      saved = saved.includes("#")
        ? saved.split("#", 1)[0]
        : saved.split("/").slice(0, -1).join("/");
    }
    focusElement(nav("item")[0] || nav("lane")[0]);
  }

  async function checkVersion() {
    try {
      const response = await fetch("render-version", {cache: "no-store"});
      const current = await response.text();
      if (checkVersion.value !== null && current !== checkVersion.value) {
        const focused = document.activeElement;
        if (focused instanceof HTMLElement) {
          const thread = focused.closest("details.thread");
          const shell = focused.closest(".item-shell,.lane-shell,.update");
          const identity = focused.dataset.focusId ||
            (thread && $(":scope > summary", thread).dataset.focusId) ||
            (shell && shell.id);
          try {
            if (identity) {
              sessionStorage.setItem("visual-brief-focus", identity);
            }
          } catch (error) {
            // Reload remains safe when storage is unavailable.
          }
        }
        location.reload();
      }
      checkVersion.value = current;
    } catch (error) {
      // The static document remains usable when the local server is absent.
    }
  }
  checkVersion.value = null;

  setupDisclosures();
  setupForms();
  setupSignals();
  filterItems("");
  focusFromSavedId();
  document.addEventListener("keydown", handleKey);
  $$(".key-control").forEach((button) => {
    button.addEventListener("click", () => runAction(button.dataset.action));
  });
  searchInput.addEventListener("input", () => filterItems(searchInput.value));
  $("#close-search").addEventListener("click", closeSearch);
  $("#close-help").addEventListener("click", closeHelp);
  help.addEventListener("keydown", trapHelpFocus);
  help.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeHelp();
  });
  checkVersion();
  window.setInterval(checkVersion, 5000);
})();
"""
