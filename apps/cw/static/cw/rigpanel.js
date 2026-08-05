/* Rig panel — polls /cw/rig/ for CAT state (freq/mode/PTT), pushes config
 * changes and tune commands. Fires opts.onState(state) so the send sheet can
 * show/hide its "key the rig" toggle. */
"use strict";

function initCWRigPanel(opts) {
  const url = opts.url;
  const el = (id) => document.getElementById(id);
  const pill = el("rig-pill");
  let lastState = null;

  function setPill(state, label) {
    pill.dataset.state = state;
    pill.textContent = label;
  }

  function renderState(data) {
    lastState = data;
    el("rig-enabled").checked = !!data.enabled;
    if (document.activeElement !== el("rig-host")) el("rig-host").value = data.host || "";
    if (document.activeElement !== el("rig-port")) el("rig-port").value = data.port || "";
    el("rig-use-ptt").checked = !!data.use_ptt;
    if (document.activeElement !== el("rig-audio-out")) el("rig-audio-out").value = data.audio_output || "";
    if (document.activeElement !== el("rig-lead")) el("rig-lead").value = data.ptt_lead_ms;

    const state = el("rig-state");
    const err = el("rig-err");
    if (!data.enabled) {
      setPill("off", "no rig");
      state.style.display = "none";
      err.textContent = "";
    } else if (data.connected) {
      const onAir = data.tx && data.tx.transmitting;
      setPill("live", onAir ? "● transmitting" : "● connected");
      state.style.display = "";
      el("rig-freq").textContent = (data.freq_hz / 1e6).toFixed(4);
      el("rig-mode").textContent = data.mode;
      el("rig-ptt").style.display = data.ptt || onAir ? "" : "none";
      err.textContent = data.tx && data.tx.error ? data.tx.error : "";
    } else {
      setPill("off", "rigctld unreachable");
      state.style.display = "none";
      err.textContent = data.error || "";
    }
    if (opts.onState) opts.onState(data);
  }

  function refresh() {
    return fetch(url, { credentials: "same-origin" })
      .then((r) => r.json())
      .then((j) => renderState(j.data || j))
      .catch(() => setPill("off", "offline"));
  }

  function post(body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then((r) => r.json().then((j) => ({ ok: r.ok, data: j.data || j })))
      .then(({ ok, data }) => {
        if (!ok) el("rig-err").textContent = data.error || data.detail || "rig error";
        else renderState(data);
      });
  }

  function configBody() {
    return {
      enabled: el("rig-enabled").checked,
      host: el("rig-host").value.trim() || "127.0.0.1",
      port: parseInt(el("rig-port").value, 10) || 4532,
      use_ptt: el("rig-use-ptt").checked,
      audio_output: el("rig-audio-out").value.trim(),
      ptt_lead_ms: parseInt(el("rig-lead").value, 10) || 150,
    };
  }

  ["rig-enabled", "rig-use-ptt"].forEach((id) =>
    el(id).addEventListener("change", () => post(configBody()))
  );
  ["rig-host", "rig-port", "rig-audio-out", "rig-lead"].forEach((id) =>
    el(id).addEventListener("change", () => post(configBody()))
  );

  el("rig-tune").addEventListener("click", () => {
    const mhz = parseFloat(el("rig-set-freq").value);
    if (!mhz) return;
    post(Object.assign(configBody(), { freq_hz: Math.round(mhz * 1e6) }));
  });
  el("rig-cw-mode").addEventListener("click", () =>
    post(Object.assign(configBody(), { mode: "CW" }))
  );

  refresh();
  const timer = setInterval(refresh, 5000);
  return {
    refresh,
    stop: () => clearInterval(timer),
    state: () => lastState,
  };
}
