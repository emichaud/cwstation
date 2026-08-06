/* Rig Setup — the launcher. Renders serial ports and Hamlib's rig catalog,
 * starts/stops the managed rigctld, and live-verifies the CAT link. */
"use strict";

function initCWRigSetup(opts) {
  const el = (id) => document.getElementById(id);
  const BAUDS = [4800, 9600, 19200, 38400, 57600, 115200];
  let models = [];
  let sel = { port: null, model: null, baud: 115200 };
  let timer = null;

  function jfetch(url, body) {
    return fetch(url, body ? {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    } : { credentials: "same-origin" })
      .then((r) => r.json().then((j) => ({ ok: r.ok, data: j.data || j })));
  }

  // ── serial ports ──────────────────────────────────────────────────────
  function renderPorts(ports) {
    const box = el("rs-ports");
    box.innerHTML = "";
    el("rs-noports").style.display = ports.length ? "none" : "";
    ports.forEach((p) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "cw-portrow" + (sel.port === p.device ? " picked" : "");
      row.innerHTML =
        '<span class="cw-mono">' + p.device + "</span>" +
        (p.hint ? '<span class="cw-porthint">' + p.hint + "</span>" : "");
      row.addEventListener("click", () => {
        sel.port = sel.port === p.device ? null : p.device;
        renderPorts(ports);
      });
      box.appendChild(row);
    });
  }

  // ── rig catalog ───────────────────────────────────────────────────────
  function renderModels(filter) {
    const box = el("rs-models");
    box.innerHTML = "";
    const f = (filter || "").toLowerCase();
    const hits = f
      ? models.filter((m) =>
          (m.mfg + " " + m.model).toLowerCase().includes(f) || String(m.id) === f)
      : models.filter((m) => m.id === sel.model);
    hits.slice(0, 12).forEach((m) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "cw-portrow" + (sel.model === m.id ? " picked" : "");
      row.innerHTML =
        '<span class="cw-mono">#' + m.id + "</span>" +
        "<span>" + m.mfg + " — <b>" + m.model + "</b></span>";
      row.addEventListener("click", () => {
        sel.model = m.id;
        el("rs-model-filter").value = m.mfg + " " + m.model;
        renderModels("");
      });
      box.appendChild(row);
    });
    if (f && !hits.length) {
      box.innerHTML = '<div class="cw-heard-empty" style="padding: 6px 0;">no match — try part of the model name</div>';
    }
  }

  function renderBauds() {
    const box = el("rs-bauds");
    box.innerHTML = "";
    BAUDS.forEach((b) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "cw-prosign" + (sel.baud === b ? " picked" : "");
      chip.textContent = String(b);
      chip.addEventListener("click", () => { sel.baud = b; renderBauds(); });
      box.appendChild(chip);
    });
  }

  // ── daemon + verify ───────────────────────────────────────────────────
  function renderDaemon(d) {
    const running = d && d.running;
    el("rs-d-state").textContent = running ? "RUN" : "OFF";
    el("rs-d-state").style.color = running ? "var(--success-fg)" : "var(--body-quiet-color)";
    el("rs-d-pid-wrap").style.display = running ? "" : "none";
    el("rs-d-model-wrap").style.display = running && d.spec ? "" : "none";
    if (running) {
      el("rs-d-pid").textContent = d.pid;
      if (d.spec) el("rs-d-model").textContent = "#" + d.spec.model;
    }
    el("rs-stop").style.display = running ? "" : "none";
    el("rs-start").style.display = running ? "none" : "";
    el("rs-dummy").style.display = running ? "none" : "";
    el("rs-log").textContent = (d && d.log && d.log.length) ? d.log.join("\n") : "—";
    el("rs-log").scrollTop = el("rs-log").scrollHeight;

    const ok = running && d.reachable;
    el("rs-verify-on").style.display = ok ? "" : "none";
    el("rs-verify-off").style.display = ok ? "none" : "";
    el("rs-v-err").textContent = (d && d.probe_error) || "";
    if (ok) {
      el("rs-v-freq").textContent = (d.freq_hz / 1e6).toFixed(4);
      el("rs-v-mode").textContent = d.mode;
      el("rs-v-ptt").style.display = d.ptt ? "" : "none";
    }
    const pill = el("rs-pill");
    pill.dataset.state = ok ? "live" : "off";
    pill.textContent = ok ? "● link verified" : running ? "daemon up, probing…" : "no daemon";
  }

  function refresh() {
    return jfetch(opts.dataUrl).then(({ data }) => {
      if (!data.hamlib || !data.hamlib.installed) {
        el("rs-nohamlib").style.display = "";
        el("rs-panels").style.opacity = 0.45;
        el("rs-pill").textContent = "hamlib missing";
        return;
      }
      el("rs-nohamlib").style.display = "none";
      el("rs-panels").style.opacity = 1;
      models = data.models || [];
      if (sel.model === null && data.saved && data.saved.rig_model) {
        sel.model = data.saved.rig_model;
        const m = models.find((x) => x.id === sel.model);
        if (m) el("rs-model-filter").value = m.mfg + " " + m.model;
      }
      if (sel.port === null && data.saved && data.saved.serial_port) sel.port = data.saved.serial_port;
      if (data.saved && data.saved.baud) sel.baud = data.saved.baud;
      renderPorts(data.serial_ports || []);
      renderModels(el("rs-model-filter").value && !sel.model ? el("rs-model-filter").value : "");
      renderBauds();
      renderDaemon(data.daemon);
    });
  }

  function daemon(body) {
    el("rs-err").textContent = "";
    return jfetch(opts.daemonUrl, body).then(({ ok, data }) => {
      if (!ok) { el("rs-err").textContent = data.error || "failed"; return; }
      renderDaemon(data.daemon);
    });
  }

  el("rs-rescan").addEventListener("click", refresh);
  el("rs-model-filter").addEventListener("input", (e) => {
    sel.model = null;
    renderModels(e.target.value);
  });
  el("rs-start").addEventListener("click", () => {
    if (!sel.model) { el("rs-err").textContent = "Pick a rig model (or use the dummy rig)."; return; }
    daemon({ action: "start", model: sel.model, serial_port: sel.port, baud: sel.baud });
  });
  el("rs-dummy").addEventListener("click", () => daemon({ action: "start", model: 1 }));
  el("rs-stop").addEventListener("click", () => daemon({ action: "stop" }));

  refresh();
  timer = setInterval(refresh, 5000);
  return { refresh, stop: () => clearInterval(timer) };
}
