/* The send sheet — slides up over the live tape like a phone keyboard.
 *
 * Opens from the docked bar or a "reply" chip (which prefills the reply and
 * the {call} context). Submits over fetch so the operator never leaves the
 * tape; the keyed session comes back as inline audio + a link.
 */
"use strict";

function initCWSendSheet(opts) {
  const sheet = document.getElementById("cw-sheet");
  const scrim = document.getElementById("cw-sheet-scrim");
  const bar = document.getElementById("cw-sendbar");
  const form = document.getElementById("cw-sheet-form");
  const ta = document.getElementById("cw-sheet-text");
  const replyBadge = document.getElementById("cw-sheet-replyto");
  const result = document.getElementById("cw-sheet-result");
  const context = Object.assign({}, opts.context || {});

  const macroCtl = initCWMacros({
    textarea: ta,
    chipsEl: document.getElementById("cw-sheet-keycaps"),
    popoverEl: document.getElementById("cw-sheet-palette"),
    url: opts.macrosUrl,
    context: context,
  });

  function open(replyCall) {
    if (replyCall) {
      context.call = replyCall;
      replyBadge.textContent = "to " + replyCall;
      replyBadge.style.display = "";
      if (!ta.value.trim()) {
        const my = (context.mycall || "").toUpperCase();
        ta.value = replyCall + " DE " + my + " " + my + " K ";
      }
    }
    sheet.classList.add("open");
    scrim.classList.add("open");
    bar.classList.add("hidden");
    setTimeout(() => {
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }, 200);
  }

  function close() {
    sheet.classList.remove("open");
    scrim.classList.remove("open");
    bar.classList.remove("hidden");
  }

  document.getElementById("cw-sendbar-open").addEventListener("click", () => open());
  document.getElementById("cw-sheet-close").addEventListener("click", close);
  scrim.addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && sheet.classList.contains("open")) {
      const palette = document.getElementById("cw-sheet-palette");
      if (!palette.classList.contains("open")) close();
    }
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text) return;
    const btn = document.getElementById("cw-sheet-send");
    btn.disabled = true;
    btn.textContent = "keying…";
    const body = new FormData(form);
    fetch(opts.sendUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      body: body,
    })
      .then((r) => r.json().then((j) => ({ ok: r.ok, data: j })))
      .then(({ ok, data }) => {
        btn.disabled = false;
        btn.textContent = "Key it";
        if (!ok) {
          result.innerHTML = '<span class="cw-errors">' +
            Object.values(data.errors || { e: ["couldn't key that"] }).join(" ") + "</span>";
          return;
        }
        result.innerHTML = "";
        const row = document.createElement("div");
        row.className = "cw-sheet-sent";
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.src = data.audio_url;
        const link = document.createElement("a");
        link.href = data.detail_url;
        link.textContent = "session #" + data.id + " ↦";
        row.append(audio, link);
        result.appendChild(row);
        audio.play().catch(() => {});
        ta.value = "";
        replyBadge.style.display = "none";
        delete context.call;
      })
      .catch(() => {
        btn.disabled = false;
        btn.textContent = "Key it";
      });
  });

  return { open, close, macroCtl };
}
