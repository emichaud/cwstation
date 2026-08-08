/* Station-callsign editor — shared by the Send and Rig Setup pages.
 *
 * POSTs {callsign} to the station endpoint, which resolves the value (falling
 * back to the username when blank) and returns {callsign, resolved}. The
 * optional onSaved(resolved) callback lets a page react — the Send page uses it
 * to update {mycall} in the live macro context with no reload.
 */
"use strict";

function initCallsignEditor(opts) {
  const input = document.getElementById(opts.inputId);
  const btn = document.getElementById(opts.saveId);
  const status = document.getElementById(opts.statusId);
  const url = opts.url;
  if (!input || !btn) return;

  function save() {
    btn.disabled = true;
    if (status) status.textContent = "Saving…";
    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ callsign: input.value }),
    })
      .then((r) => r.json().then((j) => ({ ok: r.ok, data: j.data || j })))
      .then((res) => {
        btn.disabled = false;
        if (!res.ok) { if (status) status.textContent = res.data.error || "Save failed"; return; }
        input.value = res.data.callsign;
        input.placeholder = res.data.resolved;
        if (status) {
          status.textContent = "Saved ✓";
          setTimeout(() => { status.textContent = ""; }, 2000);
        }
        if (opts.onSaved) opts.onSaved(res.data.resolved);
      })
      .catch(() => { btn.disabled = false; if (status) status.textContent = "Save failed"; });
  }

  btn.addEventListener("click", save);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); save(); } });
}
