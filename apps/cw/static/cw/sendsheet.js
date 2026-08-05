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

  // "on air" toggle — appears when a rig is connected (opts.rig.txUrl set
  // and rigReady(true) called by the rig panel)
  const rigWrap = document.getElementById("cw-sheet-rig-wrap");
  const rigCheck = document.getElementById("cw-sheet-rig");

  function rigReady(ready) {
    if (!rigWrap || !opts.rig) return;
    rigWrap.style.display = ready ? "" : "none";
    if (!ready && rigCheck) rigCheck.checked = false;
  }

  function rigTransmit(data, row) {
    return fetch(opts.rig.txUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: data.id }),
    })
      .then((r) => r.json().then((j) => ({ ok: r.ok, data: j.data || j })))
      .then(({ ok, data: tx }) => {
        const tag = document.createElement("span");
        tag.className = ok ? "cw-badge cw-badge-tx" : "cw-errors";
        tag.textContent = ok ? "ON AIR" : (tx.error || "TX failed");
        if (row) row.insertBefore(tag, row.lastChild);
      })
      .catch(() => {});
  }

  // Every send lands in the on-page transmission log (newest first) so the
  // operator can see what actually went out without leaving the tape.
  const txLog = document.getElementById("cw-txlog");
  const txRows = document.getElementById("cw-txlog-rows");

  function logSent(data) {
    if (!txLog || !txRows) return null;
    const row = document.createElement("div");
    row.className = "cw-txrow";
    const when = document.createElement("time");
    const now = new Date();
    when.textContent =
      String(now.getHours()).padStart(2, "0") + ":" +
      String(now.getMinutes()).padStart(2, "0") + ":" +
      String(now.getSeconds()).padStart(2, "0");
    const text = document.createElement("span");
    text.className = "cw-txtext";
    text.textContent = data.text;
    text.title = data.text;
    const play = document.createElement("button");
    play.type = "button";
    play.className = "cw-txplay";
    play.textContent = "▶";
    play.title = "Play as keyed";
    let player = null;
    play.addEventListener("click", () => {
      if (!player) { player = new Audio(data.audio_url); }
      player.currentTime = 0;
      player.play().catch(() => {});
    });
    const link = document.createElement("a");
    link.href = data.detail_url;
    link.textContent = "#" + data.id;
    link.title = "Open session";
    row.append(when, text, play, link);
    txRows.prepend(row);
    txLog.classList.add("has-rows");
    return row;
  }

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
        const logRow = logSent(data);
        if (rigCheck && rigCheck.checked && opts.rig) {
          rigTransmit(data, logRow);  // the rig plays it — don't double-play locally
        } else {
          audio.play().catch(() => {});
        }
        ta.value = "";
        replyBadge.style.display = "none";
        delete context.call;
      })
      .catch(() => {
        btn.disabled = false;
        btn.textContent = "Key it";
      });
  });

  return { open, close, macroCtl, rigReady };
}
