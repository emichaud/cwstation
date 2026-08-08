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
  let customImages = {}; // {modelId: url} — operator-supplied photos
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

  // ── rig-type thumbnails ────────────────────────────────────────────────
  // Original illustrations by rig archetype (not manufacturer photos — those
  // are copyrighted). Tuned against the real Hamlib catalog: base station,
  // compact/QRP, mobile, handheld, communications receiver, or software/SDR.
  // Rules are ordered most-specific first; the default is a base transceiver.
  function rigArchetype(mfg, model, id) {
    const s = (mfg + " " + model).toLowerCase();
    const M = mfg.toLowerCase();

    // 1 · software / SDR / PC-controlled (no physical front panel)
    if (id === 1 || M === "flexradio" || M === "tapr" ||
        /hamlib|flrig|trxmanager|gnu ?radio|net ?rigctl|dummy/.test(s) ||
        /power ?sdr|hpsdr|perseus|winradio|softrock|\bsdr\b/.test(s) ||
        /ic-?pcr|\brx-3\d\d\b|505dsp|kachina/.test(s))
      return "software";

    // 2 · handheld (HTs + handheld receivers / scanners)
    if (M === "uniden" || /\bth-|\bdj-|handheld|handie|\bvx-/.test(s) ||
        /id-?(31|51|52)\b|ic-?92|ic-?2gx|ic-?r(6|10|20|30)\b|ic-?rx7/.test(s))
      return "handheld";

    // 3 · communications receiver (RX-only desktop)
    if (M === "aor" || M === "jrc" || M === "drake" || M === "racal" ||
        M === "skanti" || M === "rohde&schwarz" || M === "barrett" ||
        M === "codan" || M === "optoelectronics" ||
        /ic-?r(71|72|75|7000|7100|8500|8600|9000|9500)\b/.test(s) ||
        /\bfrg-|\bvr-?5000|\br-?5000\b|\bnrd-/.test(s))
      return "receiver";

    // 4 · mobile (dual-band mobiles + mobile HF)
    if (/\btm-|ftm-|ic-?2730|id-?(4100|5100)/.test(s) ||
        /ic-?706|ic-?7000\b|ft-?857|ft-?897|ft-?100\b/.test(s))
      return "mobile";

    // 5 · compact / QRP / portable
    if (M === "qrplabs" ||
        /kx[0-9]|xiegu|x108|x5105|x6100|x6200|g90|g106|ic-?703|ic-?705|ft-?81[78]|\bqrp\b|qcx|\bmtr\b|argonaut|tt-?516/.test(s))
      return "compact";

    // 6 · base HF/VHF transceiver (default)
    return "base";
  }
  if (typeof window !== "undefined") window.cwRigArchetype = rigArchetype; // test seam

  const THUMBS = {
    base:
      '<rect x="1" y="4" width="46" height="25" rx="4" fill="#232a34" stroke="#3a4150"/>' +
      '<rect x="4" y="8" width="22" height="17" rx="2" fill="#0b0e13"/>' +
      '<rect x="6" y="12" width="15" height="3" rx="1" fill="#e0b84c"/>' +
      '<rect x="6" y="18" width="9" height="2" rx="1" fill="#7a6733"/>' +
      '<circle cx="38" cy="16" r="7.5" fill="#161b22" stroke="#3a4150"/>' +
      '<circle cx="38" cy="10.5" r="1.6" fill="#e0b84c"/>',
    compact:
      '<rect x="8" y="6" width="32" height="21" rx="4" fill="#232a34" stroke="#3a4150"/>' +
      '<rect x="11" y="10" width="15" height="12" rx="2" fill="#0b0e13"/>' +
      '<rect x="13" y="13" width="10" height="3" rx="1" fill="#e0b84c"/>' +
      '<circle cx="33" cy="16" r="5" fill="#161b22" stroke="#3a4150"/>',
    mobile:
      '<rect x="2" y="9" width="38" height="15" rx="3" fill="#232a34" stroke="#3a4150"/>' +
      '<rect x="5" y="12" width="20" height="9" rx="1.5" fill="#0b0e13"/>' +
      '<rect x="7" y="14.5" width="13" height="3" rx="1" fill="#e0b84c"/>' +
      '<circle cx="33" cy="16.5" r="4" fill="#161b22" stroke="#3a4150"/>' +
      '<rect x="42" y="7" width="4" height="20" rx="2" fill="#2b323d"/>',
    handheld:
      '<rect x="17" y="6" width="16" height="24" rx="3" fill="#232a34" stroke="#3a4150"/>' +
      '<rect x="28" y="1" width="2.5" height="7" rx="1" fill="#3a4150"/>' +
      '<rect x="20" y="9" width="10" height="7" rx="1.5" fill="#0b0e13"/>' +
      '<rect x="21.5" y="11" width="7" height="2.5" rx="1" fill="#e0b84c"/>' +
      '<circle cx="22" cy="21" r="1.4" fill="#3a4150"/><circle cx="25" cy="21" r="1.4" fill="#3a4150"/>' +
      '<circle cx="28" cy="21" r="1.4" fill="#3a4150"/><circle cx="23.5" cy="25" r="1.4" fill="#3a4150"/>' +
      '<circle cx="26.5" cy="25" r="1.4" fill="#3a4150"/>',
    receiver:
      '<rect x="1" y="6" width="46" height="21" rx="3" fill="#232a34" stroke="#3a4150"/>' +
      '<rect x="4" y="10" width="16" height="13" rx="2" fill="#0b0e13"/>' +
      '<rect x="6" y="13" width="11" height="3" rx="1" fill="#e0b84c"/>' +
      '<circle cx="24" cy="13" r="1" fill="#3a4150"/><circle cx="28" cy="13" r="1" fill="#3a4150"/>' +
      '<circle cx="32" cy="13" r="1" fill="#3a4150"/><circle cx="24" cy="17" r="1" fill="#3a4150"/>' +
      '<circle cx="28" cy="17" r="1" fill="#3a4150"/><circle cx="32" cy="17" r="1" fill="#3a4150"/>' +
      '<circle cx="24" cy="21" r="1" fill="#3a4150"/><circle cx="28" cy="21" r="1" fill="#3a4150"/>' +
      '<circle cx="32" cy="21" r="1" fill="#3a4150"/>' +
      '<circle cx="41" cy="16.5" r="5" fill="#161b22" stroke="#3a4150"/>',
    software:
      '<rect x="3" y="4" width="42" height="22" rx="3" fill="#232a34" stroke="#3a4150"/>' +
      '<rect x="6" y="7" width="36" height="16" rx="2" fill="#0b0e13"/>' +
      '<polyline points="9,15 15,15 18,10 22,20 26,12 30,17 33,15 39,15" fill="none" ' +
      'stroke="#e0b84c" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>' +
      '<rect x="20" y="26" width="8" height="3" rx="1" fill="#2b323d"/>',
  };
  function rigThumb(m) {
    const img = customImages[m.id];
    if (img) {
      return '<span class="cw-model-thumb"><img src="' + img + '" alt="" ' +
        'onerror="this.parentNode.classList.add(\'broken\')"></span>';
    }
    const kind = rigArchetype(m.mfg, m.model, m.id);
    return '<span class="cw-model-thumb"><svg viewBox="0 0 48 32" aria-hidden="true">' +
      (THUMBS[kind] || THUMBS.base) + "</svg></span>";
  }

  // ── the rig illustration (big, per selected model) ─────────────────────
  function fmtFreq(hz) {
    if (!hz) return "—.———";
    return (hz / 1e6).toFixed(4);
  }
  // Large hero illustrations, one per archetype. Each has an .rig-freq text,
  // an optional .rig-mode, and a .rig-led power light that JS updates. The
  // frame uses the --rig-* CSS variables; screen/LED glow in the palette.
  function bigRig(kind) {
    const S = 'fill="var(--rig-body)" stroke="var(--card-border)" stroke-width="2"';
    const LCD = 'fill="var(--rig-lcd)" stroke="var(--card-border)"';
    const led = (cx, cy) => '<circle class="rig-led" cx="' + cx + '" cy="' + cy + '" r="7" fill="var(--rig-led-off)"/>';
    const freq = (x, y, sz) => '<text class="rig-freq" x="' + x + '" y="' + y + '" text-anchor="middle"' +
      (sz ? ' style="font-size:' + sz + 'px"' : "") + ">—.———</text>";
    const knob = (cx, cy, r) =>
      '<circle cx="' + cx + '" cy="' + cy + '" r="' + (r + 8) + '" fill="var(--rig-knob-ring)"/>' +
      '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="var(--rig-knob)" stroke="var(--card-border)" stroke-width="2"/>' +
      '<circle cx="' + cx + '" cy="' + (cy - r + 6) + '" r="6" fill="var(--primary)"/>';
    const smeter = (x, y) => {
      let g = "";
      const h = [16, 20, 24, 18, 14, 12], dim = [0, 0, 0, 1, 1, 1];
      for (let i = 0; i < 6; i++)
        g += '<rect x="' + (x + i * 11) + '" y="' + (y + (24 - h[i])) + '" width="8" height="' + h[i] +
          '" rx="1" fill="var(--rig-smeter' + (dim[i] ? "-dim" : "") + ')"/>';
      return g;
    };
    const svg = (inner) => '<svg viewBox="0 0 400 210" class="cw-rig-svg" role="img" aria-label="Radio">' + inner + "</svg>";

    if (kind === "software") {
      return svg(
        '<rect x="70" y="14" width="260" height="150" rx="12" ' + S + "/>" +
        '<rect x="84" y="28" width="232" height="112" rx="6" ' + LCD + "/>" +
        '<polyline points="94,96 118,96 130,66 146,126 162,80 178,110 196,96 306,96" fill="none" ' +
        'stroke="var(--primary)" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>' +
        freq(200, 52, 22) +
        '<rect x="150" y="164" width="100" height="10" rx="2" fill="var(--rig-top)"/>' +
        '<rect x="120" y="174" width="160" height="12" rx="3" ' + S + "/>" + led(300, 34));
    }
    if (kind === "handheld") {
      return svg(
        '<rect x="150" y="14" width="100" height="182" rx="16" ' + S + "/>" +
        '<rect x="236" y="-2" width="10" height="24" rx="4" fill="var(--rig-top)"/>' +   // antenna
        '<rect x="164" y="34" width="72" height="52" rx="6" ' + LCD + "/>" +
        freq(200, 66, 22) +
        smeter(172, 96).replace(/width="8"/g, 'width="6"') +
        // keypad
        (function () {
          let k = ""; for (let r = 0; r < 4; r++) for (let c = 0; c < 3; c++)
            k += '<rect x="' + (166 + c * 24) + '" y="' + (120 + r * 18) + '" width="18" height="13" rx="3" fill="var(--rig-btn)"/>';
          return k;
        })() + led(232, 26));
    }
    if (kind === "receiver") {
      let grille = "";
      for (let r = 0; r < 4; r++) for (let c = 0; c < 5; c++)
        grille += '<circle cx="' + (250 + c * 15) + '" cy="' + (74 + r * 15) + '" r="2.6" fill="var(--rig-knob-ring)"/>';
      return svg(
        '<rect x="6" y="20" width="388" height="170" rx="14" ' + S + "/>" +
        '<rect x="6" y="20" width="388" height="18" rx="14" fill="var(--rig-top)"/>' +
        '<rect x="26" y="58" width="180" height="94" rx="8" ' + LCD + "/>" +
        freq(116, 104) + smeter(150, 118) +
        grille + knob(340, 105, 34) + led(372, 40));
    }
    if (kind === "compact") {
      return svg(
        '<rect x="52" y="46" width="296" height="120" rx="12" ' + S + "/>" +
        '<rect x="70" y="66" width="150" height="80" rx="6" ' + LCD + "/>" +
        freq(145, 108) + smeter(110, 122) +
        knob(292, 106, 32) +
        '<rect x="70" y="150" width="26" height="10" rx="2" fill="var(--rig-btn)"/>' +
        '<rect x="102" y="150" width="26" height="10" rx="2" fill="var(--rig-btn)"/>' +
        led(330, 60));
    }
    if (kind === "mobile") {
      return svg(
        '<rect x="20" y="66" width="300" height="86" rx="12" ' + S + "/>" +
        '<rect x="20" y="66" width="300" height="14" rx="12" fill="var(--rig-top)"/>' +
        '<rect x="38" y="92" width="150" height="48" rx="5" ' + LCD + "/>" +
        freq(113, 124) + knob(272, 109, 26) +
        // mic on a cord
        '<path d="M320 150 q40 20 56 -6" fill="none" stroke="var(--card-border)" stroke-width="2"/>' +
        '<rect x="368" y="120" width="22" height="40" rx="6" ' + S + "/>" +
        '<circle cx="379" cy="134" r="5" fill="var(--rig-knob-ring)"/>' + led(300, 73));
    }
    // base transceiver (default)
    return svg(
      '<rect x="6" y="8" width="388" height="194" rx="16" ' + S + "/>" +
      '<rect x="6" y="8" width="388" height="20" rx="16" fill="var(--rig-top)"/>' +
      '<rect x="24" y="44" width="210" height="112" rx="8" ' + LCD + "/>" +
      freq(129, 98) +
      '<text class="rig-mode cw-rig-lcd-lab" x="40" y="140">———</text>' +
      smeter(150, 120) + knob(318, 112, 46) +
      '<rect x="24" y="168" width="34" height="18" rx="4" fill="var(--rig-btn)"/>' +
      '<rect x="66" y="168" width="34" height="18" rx="4" fill="var(--rig-btn)"/>' +
      '<rect x="108" y="168" width="34" height="18" rx="4" fill="var(--rig-btn)"/>' + led(372, 30));
  }

  let currentArt = "";
  function renderRig(daemon) {
    const connected = daemon && daemon.running && daemon.reachable;
    const kind = sel.dummy ? "software"
      : sel.model ? rigArchetype(
          (models.find((x) => x.id === sel.model) || {}).mfg || "",
          (models.find((x) => x.id === sel.model) || {}).model || "", sel.model)
      : "base";

    // custom photo (operator-supplied) wins over the illustration
    const photo = sel.model && !sel.dummy && customImages[sel.model];
    const photoEl = el("rs-rig-photo"), artEl = el("rs-rig-art");
    if (photo) {
      photoEl.src = photo;
      photoEl.style.display = "";
      artEl.style.display = "none";
    } else {
      photoEl.style.display = "none";
      artEl.style.display = "";
      if (currentArt !== kind) { artEl.innerHTML = bigRig(kind); currentArt = kind; }
      const freq = connected ? fmtFreq(daemon.freq_hz) : "—.———";
      const q = (c) => artEl.querySelector("." + c);
      if (q("rig-freq")) q("rig-freq").textContent = freq;
      if (q("rig-mode")) q("rig-mode").textContent = connected ? daemon.mode : "———";
      if (q("rig-led")) q("rig-led").setAttribute("fill", connected ? "var(--rig-led-on)" : "var(--rig-led-off)");
    }
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
        rigThumb(m) +
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
      customImages = data.custom_images || {};
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
