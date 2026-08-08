/* Message keys — the Send composer's keyer memory bank.
 *
 * Two ways to fire a macro:
 *   1. Keycap chips under the composer (click = insert at caret)
 *   2. The slash palette: type "/" at a word boundary in the textarea and a
 *      filterable command palette opens. ↑↓ navigate, Enter/Tab insert,
 *      Esc dismisses.
 *
 * Placeholders expand at insert time: known context ({mycall}, {call},
 * {rst}) fills in; the first unknown {placeholder} is left selected in the
 * textarea so the operator can type straight over it — snippet-style.
 *
 * Colors are theme variables throughout; nothing here fights the palettes.
 */
"use strict";

function initCWMacros(opts) {
  const ta = opts.textarea;
  const chipsEl = opts.chipsEl;
  const popEl = opts.popoverEl;
  const bankEl = opts.bankEl; // management rows (optional)
  const url = opts.url;
  const varsUrl = opts.varsUrl; // custom-tag endpoint (optional)
  const varsEl = opts.varsEl; // custom-tag management rows (optional)
  const context = opts.context || {}; // {mycall, call, rst} + custom tags

  // Names the station fills — a custom tag can never shadow these.
  const RESERVED = new Set(["mycall", "call", "rst"]);

  let macros = [];
  let vars = [];
  let palette = { open: false, filter: "", index: 0, tokenStart: -1 };

  // ── expansion ─────────────────────────────────────────────────────────
  function expand(text) {
    // returns {value, selStart, selEnd} — selection covers the first
    // unresolved placeholder, if any
    let out = "";
    let sel = null;
    const re = /\{([a-z0-9_-]+)\}/gi;
    let last = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      out += text.slice(last, m.index);
      const key = m[1].toLowerCase();
      const known = context[key];
      if (known) {
        out += String(known).toUpperCase();
      } else {
        if (sel === null) sel = { start: out.length, end: out.length + m[0].length };
        out += m[0];
      }
      last = m.index + m[0].length;
    }
    out += text.slice(last);
    return { value: out, sel };
  }

  function insert(macro) {
    const from = palette.open ? palette.tokenStart : ta.selectionStart;
    const to = ta.selectionEnd;
    const expanded = expand(macro.text);
    const before = ta.value.slice(0, from);
    const after = ta.value.slice(to);
    const lead = before && !/\s$/.test(before) ? " " : "";
    const tail = /^\s/.test(after) || after === "" ? " " : "";
    const chunk = lead + expanded.value + tail;
    ta.value = before + chunk + after;
    if (expanded.sel) {
      const base = before.length + lead.length;
      ta.setSelectionRange(base + expanded.sel.start, base + expanded.sel.end);
    } else {
      const pos = before.length + chunk.length;
      ta.setSelectionRange(pos, pos);
    }
    closePalette();
    ta.focus();
  }

  // ── slash palette ─────────────────────────────────────────────────────
  function matches() {
    const f = palette.filter.toLowerCase();
    return macros.filter(
      (m) => m.name.startsWith(f) || m.text.toLowerCase().includes(f)
    );
  }

  function openPalette(tokenStart) {
    palette = { open: true, filter: "", index: 0, tokenStart };
    renderPalette();
  }

  function closePalette() {
    if (!palette.open) return;
    palette.open = false;
    popEl.classList.remove("open");
    popEl.innerHTML = "";
  }

  function highlightToken(text) {
    // escape, then wrap {placeholders} as tokens
    const esc = text.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
    return esc.replace(/\{[a-z0-9_]+\}/gi, (t) => '<span class="cw-ph">' + t + "</span>");
  }

  function renderPalette() {
    const rows = matches();
    if (!rows.length) { closePalette(); return; }
    if (palette.index >= rows.length) palette.index = rows.length - 1;
    popEl.innerHTML = "";
    const head = document.createElement("div");
    head.className = "cw-palette-head";
    head.innerHTML =
      '<span>message keys</span>' +
      '<span class="cw-palette-hints"><kbd>↑↓</kbd><kbd>↵</kbd> insert <kbd>esc</kbd></span>';
    popEl.appendChild(head);
    rows.forEach((m, i) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "cw-palette-row" + (i === palette.index ? " active" : "");
      row.innerHTML =
        '<span class="cw-palette-name">/' + m.name + "</span>" +
        '<span class="cw-palette-text">' + highlightToken(m.text) + "</span>";
      row.addEventListener("mousedown", (e) => { e.preventDefault(); insert(m); });
      row.addEventListener("mousemove", () => {
        if (palette.index !== i) { palette.index = i; renderPalette(); }
      });
      popEl.appendChild(row);
    });
    popEl.classList.add("open");
  }

  function syncPaletteFromCaret() {
    // a palette session is live while the text from tokenStart to the caret
    // is "/word"
    const upto = ta.value.slice(0, ta.selectionStart);
    const m = upto.match(/(^|\s)\/([a-z0-9-]*)$/i);
    if (!m) { closePalette(); return; }
    const tokenStart = upto.length - m[2].length - 1;
    if (!palette.open) openPalette(tokenStart);
    palette.tokenStart = tokenStart;
    palette.filter = m[2];
    renderPalette();
  }

  ta.addEventListener("input", syncPaletteFromCaret);
  ta.addEventListener("click", () => { if (palette.open) syncPaletteFromCaret(); });
  ta.addEventListener("blur", () => setTimeout(closePalette, 150));
  ta.addEventListener("keydown", (e) => {
    if (!palette.open) return;
    const rows = matches();
    if (e.key === "ArrowDown") {
      e.preventDefault();
      palette.index = (palette.index + 1) % rows.length;
      renderPalette();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      palette.index = (palette.index - 1 + rows.length) % rows.length;
      renderPalette();
    } else if (e.key === "Enter" || e.key === "Tab") {
      if (rows.length) { e.preventDefault(); insert(rows[palette.index]); }
    } else if (e.key === "Escape") {
      e.preventDefault();
      closePalette();
    }
  });

  // ── keycap chips ──────────────────────────────────────────────────────
  function renderChips() {
    chipsEl.innerHTML = "";
    macros.forEach((m) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "cw-keycap";
      chip.title = m.text;
      chip.textContent = "/" + m.name;
      chip.addEventListener("click", () => insert(m));
      chipsEl.appendChild(chip);
    });
  }

  // ── memory bank (inline management) ───────────────────────────────────
  function postTo(u, body) {
    return fetch(u, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.json().then((j) => ({ ok: r.ok, data: j.data || j })));
  }
  function post(body) { return postTo(url, body); }

  function renderBank() {
    if (!bankEl) return;
    bankEl.innerHTML = "";
    macros.forEach((m) => bankEl.appendChild(bankRow(m)));
    const add = document.createElement("button");
    add.type = "button";
    add.className = "cw-bank-add";
    add.textContent = "+ new message key";
    add.addEventListener("click", () => {
      add.replaceWith(bankRow(null));
    });
    bankEl.appendChild(add);
  }

  function bankRow(m) {
    const row = document.createElement("div");
    row.className = "cw-bank-row";
    const name = document.createElement("input");
    name.className = "cw-bank-name cw-mono";
    name.value = m ? "/" + m.name : "/";
    name.spellcheck = false;
    name.setAttribute("aria-label", "Command name");
    const text = document.createElement("input");
    text.className = "cw-bank-text cw-mono";
    text.value = m ? m.text : "";
    text.placeholder = "MESSAGE TO KEY — {call} {mycall} {rst} EXPAND";
    text.spellcheck = false;
    text.setAttribute("aria-label", "Message text");
    const del = document.createElement("button");
    del.type = "button";
    del.className = "cw-bank-del";
    del.textContent = "✕";
    del.title = m ? "Delete /" + m.name : "Discard";
    const err = document.createElement("span");
    err.className = "cw-bank-err";

    function save() {
      const body = { name: name.value, text: text.value };
      if (m) body.id = m.id;
      if (!name.value.replace("/", "").trim() || !text.value.trim()) return;
      post(body).then(({ ok, data }) => {
        if (!ok) { err.textContent = data.error || data.detail || "couldn't save"; return; }
        err.textContent = "";
        refresh();
      });
    }
    name.addEventListener("change", save);
    text.addEventListener("change", save);
    [name, text].forEach((inp) =>
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); inp.blur(); } })
    );
    del.addEventListener("click", () => {
      if (m) post({ id: m.id, delete: true }).then(refresh);
      else row.remove();
    });
    row.append(name, text, del, err);
    return row;
  }

  function refresh() {
    return fetch(url, { credentials: "same-origin" })
      .then((r) => r.json())
      .then((j) => {
        macros = (j.data || j).macros || [];
        renderChips();
        renderBank();
      });
  }

  // ── custom tags (user-defined {name} → value) ─────────────────────────
  function applyVars() {
    // reset context to just its reserved keys, then layer tags underneath by
    // rebuilding: reserved values are re-applied last so a tag can't shadow them
    vars.forEach((v) => { if (!RESERVED.has(v.name)) context[v.name] = v.value; });
  }

  function renderVarBank() {
    if (!varsEl) return;
    varsEl.innerHTML = "";
    vars.forEach((v) => varsEl.appendChild(varRow(v)));
    const add = document.createElement("button");
    add.type = "button";
    add.className = "cw-bank-add";
    add.textContent = "+ new tag";
    add.addEventListener("click", () => { add.replaceWith(varRow(null)); });
    varsEl.appendChild(add);
  }

  function varRow(v) {
    const row = document.createElement("div");
    row.className = "cw-bank-row";
    const name = document.createElement("input");
    name.className = "cw-bank-name cw-mono";
    name.value = v ? "{" + v.name + "}" : "{";
    name.spellcheck = false;
    name.setAttribute("aria-label", "Tag name");
    const value = document.createElement("input");
    value.className = "cw-bank-text cw-mono";
    value.value = v ? v.value : "";
    value.placeholder = "VALUE — e.g. KW4420";
    value.spellcheck = false;
    value.setAttribute("aria-label", "Tag value");
    const del = document.createElement("button");
    del.type = "button";
    del.className = "cw-bank-del";
    del.textContent = "✕";
    del.title = v ? "Delete {" + v.name + "}" : "Discard";
    const err = document.createElement("span");
    err.className = "cw-bank-err";

    function save() {
      const cleanName = name.value.replace(/[{}]/g, "").trim();
      if (!cleanName || !value.value.trim()) return;
      const body = { name: cleanName, value: value.value };
      if (v) body.id = v.id;
      postTo(varsUrl, body).then(({ ok, data }) => {
        if (!ok) { err.textContent = data.error || data.detail || "couldn't save"; return; }
        err.textContent = "";
        refreshVars();
      });
    }
    name.addEventListener("change", save);
    value.addEventListener("change", save);
    [name, value].forEach((inp) =>
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); inp.blur(); } })
    );
    del.addEventListener("click", () => {
      if (v) postTo(varsUrl, { id: v.id, delete: true }).then(refreshVars);
      else row.remove();
    });
    row.append(name, value, del, err);
    return row;
  }

  function refreshVars() {
    if (!varsUrl) return Promise.resolve();
    return fetch(varsUrl, { credentials: "same-origin" })
      .then((r) => r.json())
      .then((j) => {
        vars = (j.data || j).vars || [];
        applyVars();
        renderVarBank();
      });
  }

  refresh();
  refreshVars();
  return { refresh, refreshVars, insert, expand };
}
