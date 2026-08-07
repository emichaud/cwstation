/* Rig Setup — connect a radio through Hamlib's rigctld, made friendly.
 *
 * Drives: rig picker (practice/dummy + brand chips + model search + serial
 * port), the live rig illustration, a three-light service panel, and the
 * connect / restart / disconnect flow. */
"use strict";

function initCWRigSetup(opts) {
  const el = (id) => document.getElementById(id);
  const BAUDS = [4800, 9600, 19200, 38400, 57600, 115200];
  // brands shown as quick filters when present in the Hamlib catalog
  const BRANDS = ["Icom", "Yaesu", "Kenwood", "Elecraft", "FlexRadio",
                  "Ten-Tec", "Alinco", "Xiegu"];

  let models = [];
  let sel = { port: null, model: null, baud: 115200, dummy: false };
  let mfgFilter = "";

  function jfetch(url, body) {
    return fetch(url, body ? {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    } : { credentials: "same-origin" })
      .then((r) => r.json().then((j) => ({ ok: r.ok, data: j.data || j })));
  }

  // ── the rig illustration ──────────────────────────────────────────────
  function fmtFreq(hz) {
    if (!hz) return "—.———";
    return (hz / 1e6).toFixed(4);
  }
  function renderRig(daemon) {
    const isSw = sel.dummy;
    el("rig-hw").style.display = isSw ? "none" : "";
    el("rig-sw").style.display = isSw ? "" : "none";
    const connected = daemon && daemon.running && daemon.reachable;
    const freq = connected ? fmtFreq(daemon.freq_hz) : "—.———";
    el("rig-hw-freq").textContent = freq;
    el("rig-sw-freq").textContent = freq;
    el("rig-hw-mode").textContent = connected ? daemon.mode : "———";
    el("rig-led").setAttribute("fill", connected ? "var(--rig-led-on)" : "var(--rig-led-off)");
    el("rs-rig").classList.toggle("live", !!connected);

    let plate = "No radio selected";
    if (sel.dummy) plate = "Software Test Rig";
    else if (sel.model) {
      const m = models.find((x) => x.id === sel.model);
      plate = m ? m.mfg + " " + m.model : "Model #" + sel.model;
    }
    el("rs-rig-plate").textContent = plate;
  }

  // ── selector ──────────────────────────────────────────────────────────
  function renderBrands() {
    const have = new Set(models.map((m) => m.mfg));
    const box = el("rs-mfgs");
    box.innerHTML = "";
    BRANDS.filter((b) => [...have].some((h) => h.toLowerCase().includes(b.toLowerCase())))
      .forEach((brand) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "cw-mfg-chip" + (mfgFilter === brand ? " picked" : "");
        chip.textContent = brand;
        chip.addEventListener("click", () => {
          mfgFilter = mfgFilter === brand ? "" : brand;
          el("rs-model-filter").value = "";
          renderBrands();
          renderModels();
        });
        box.appendChild(chip);
      });
  }

  function renderModels() {
    const box = el("rs-models");
    box.innerHTML = "";
    const q = el("rs-model-filter").value.trim().toLowerCase();
    let hits = models;
    if (mfgFilter) hits = hits.filter((m) => m.mfg.toLowerCase().includes(mfgFilter.toLowerCase()));
    if (q) hits = hits.filter((m) => (m.mfg + " " + m.model).toLowerCase().includes(q) || String(m.id) === q);
    else if (!mfgFilter && !sel.model) hits = [];  // nothing until they pick a brand or search
    else if (!q && sel.model && !mfgFilter) hits = hits.filter((m) => m.id === sel.model);

    if (!hits.length) {
      box.innerHTML = q || mfgFilter
        ? '<div class="cw-setup-note" style="padding: 10px 0;">No match — try part of the model name.</div>'
        : "";
      return;
    }
    hits.slice(0, 40).forEach((m) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "cw-model-row" + (sel.model === m.id && !sel.dummy ? " picked" : "");
      row.innerHTML =
        '<span class="cw-model-name">' + m.mfg + " " + m.model + "</span>" +
        '<span class="cw-model-id cw-mono">#' + m.id + "</span>";
      row.addEventListener("click", () => {
        sel.model = m.id;
        sel.dummy = false;
        renderModels();
        markDummy();
        updateConnectEnabled();
        renderRig(lastDaemon);
      });
      box.appendChild(row);
    });
  }

  function renderPorts(ports) {
    const box = el("rs-ports");
    box.innerHTML = "";
    el("rs-noports").style.display = ports.length ? "none" : "";
    ports.forEach((p) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "cw-model-row" + (sel.port === p.device ? " picked" : "");
      row.innerHTML =
        '<span class="cw-model-name cw-mono">' + p.device + "</span>" +
        (p.hint ? '<span class="cw-port-hint">' + p.hint + "</span>" : "");
      row.addEventListener("click", () => {
        sel.port = sel.port === p.device ? null : p.device;
        renderPorts(ports);
      });
      box.appendChild(row);
    });
  }

  function renderBauds() {
    const box = el("rs-bauds");
    box.innerHTML = "";
    BAUDS.forEach((b) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "cw-mfg-chip" + (sel.baud === b ? " picked" : "");
      chip.textContent = b;
      chip.addEventListener("click", () => { sel.baud = b; renderBauds(); });
      box.appendChild(chip);
    });
  }

  function markDummy() {
    el("rs-dummy").classList.toggle("picked", sel.dummy);
  }
  function updateConnectEnabled() {
    el("rs-connect").disabled = !(sel.dummy || sel.model);
  }

  el("rs-dummy").addEventListener("click", () => {
    sel.dummy = true;
    sel.model = 1;
    markDummy();
    renderModels();
    updateConnectEnabled();
    renderRig(lastDaemon);
  });
  el("rs-model-filter").addEventListener("input", () => { mfgFilter = ""; renderBrands(); renderModels(); });
  el("rs-rescan").addEventListener("click", refresh);

  // ── stoplights ────────────────────────────────────────────────────────
  function setLight(n, state, status) {
    el("light-" + n).dataset.state = state;
    el("status-" + n).textContent = status;
  }

  let lastDaemon = null;
  function renderStatus(data) {
    const hamlib = data.hamlib && data.hamlib.installed;
    const daemon = data.daemon || {};
    lastDaemon = daemon;
    const running = !!daemon.running;
    const linked = running && daemon.reachable;

    // light 1 — hamlib
    setLight(1, hamlib ? "green" : "red",
      hamlib ? "installed — " + (data.hamlib.version || "ready") : "not installed (see above)");
    // light 2 — daemon
    setLight(2, running ? "green" : "off",
      running ? "running (process " + daemon.pid + ")" : "not started");
    // light 3 — CAT link
    setLight(3,
      linked ? "green" : running ? "amber" : "off",
      linked ? "connected — reading the dial" :
        running ? (daemon.probe_error || "starting up…") : "not started");

    // buttons
    el("rs-connect").style.display = running ? "none" : "";
    el("rs-restart").style.display = running ? "" : "none";
    el("rs-disconnect").style.display = running ? "" : "none";
    updateConnectEnabled();

    // step 3
    el("rs-done").style.display = linked ? "" : "none";
    if (linked) {
      el("rs-v-freq").textContent = fmtFreq(daemon.freq_hz);
      el("rs-v-mode").textContent = daemon.mode;
      el("rs-v-ptt").style.display = daemon.ptt ? "" : "none";
    }

    el("rs-log").textContent = (daemon.log && daemon.log.length) ? daemon.log.join("\n") : "—";
    const pill = el("rs-pill");
    pill.dataset.state = linked ? "live" : "off";
    pill.textContent = linked ? "● connected" : running ? "starting…" : "not connected";

    renderRig(daemon);
  }

  // ── data + actions ────────────────────────────────────────────────────
  function refresh() {
    return jfetch(opts.dataUrl).then(({ data }) => {
      const hamlib = data.hamlib && data.hamlib.installed;
      el("rs-nohamlib").style.display = hamlib ? "none" : "";
      el("rs-panels").style.opacity = hamlib ? 1 : 0.5;
      el("rs-panels").style.pointerEvents = hamlib ? "" : "none";

      models = data.models || [];
      // restore saved choice
      if (sel.model === null && sel.dummy === false && data.saved) {
        if (data.saved.rig_model === 1) { sel.dummy = true; sel.model = 1; }
        else if (data.saved.rig_model) { sel.model = data.saved.rig_model; }
        if (data.saved.serial_port) sel.port = data.saved.serial_port;
        if (data.saved.baud) sel.baud = data.saved.baud;
      }
      renderBrands();
      renderModels();
      renderPorts(data.serial_ports || []);
      renderBauds();
      markDummy();
      renderStatus(data);
    });
  }

  function connectBody() {
    return sel.dummy
      ? { action: "start", model: 1 }
      : { action: "start", model: sel.model, serial_port: sel.port, baud: sel.baud };
  }
  function daemon(body) {
    el("rs-err").textContent = "";
    return jfetch(opts.daemonUrl, body).then(({ ok, data }) => {
      if (!ok) { el("rs-err").textContent = data.error || "Couldn't start — check the connection and try again."; }
      return refresh();
    });
  }

  el("rs-connect").addEventListener("click", () => {
    if (!(sel.dummy || sel.model)) {
      el("rs-err").textContent = "Pick a radio first (or the Test radio).";
      return;
    }
    daemon(connectBody());
  });
  el("rs-restart").addEventListener("click", () => {
    daemon({ action: "stop" }).then(() => daemon(connectBody()));
  });
  el("rs-disconnect").addEventListener("click", () => daemon({ action: "stop" }));

  refresh();
  const timer = setInterval(refresh, 4000);
  return { refresh, stop: () => clearInterval(timer) };
}
