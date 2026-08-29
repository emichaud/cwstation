/* FM Radio faceplate — tune rtl_fm, and keep six presets like a car stereo.
 *
 * Python does the radio work; this only sends tune/stop and renders state
 * (same division as the rest of the console). While playing we poll the
 * control endpoint so the UI notices if rtl_fm dies on its own.
 */
(function () {
  "use strict";
  var cfg = window.CW_RADIO || {};
  if (!cfg.controlUrl) return;

  var $ = function (id) { return document.getElementById(id); };
  var chassis = $("fm-chassis");
  var pill = $("fm-pill");
  var freq = $("fm-freq");
  var freqVal = $("fm-freq-val");
  var status = $("fm-status");
  var favs = $("fm-favs");
  var favsEmpty = $("fm-favs-empty");
  var deviceNote = $("fm-device-note");
  var problems = $("fm-device-detail");

  var PRESET_SLOTS = 6;
  var stations = [];
  var state = {};
  var poll = null;

  function get(url) {
    return fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) { return j.data || j; });
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().then(function (j) {
        return { ok: r.ok, data: j.data || j, error: j.error || (j.data && j.data.error) };
      });
    });
  }

  function say(msg, kind) {
    status.textContent = msg || "";
    status.dataset.kind = kind || "";
  }

  function showFreq(mhz) {
    // The display holds seven-segment digits only; the MHz unit is printed on
    // the glass. DSEG renders "!" as a blank cell — pad so 88.1 sits where
    // 108.1 would, and the digits don't slide around while tuning.
    var text = Number(mhz).toFixed(1);
    freqVal.textContent = (text.length < 5 ? "!" : "") + text;
  }

  /* ---- receiver presence (the model plate) -------------------------------
   * Three independent failures, each with its own fix, so the lamps name them
   * apart and the problems box spells out the commands.
   */
  function renderDevice() {
    var devices = state.devices || [];
    var lamps = {
      "fm-lamp-ant": devices.length > 0,
      "fm-lamp-rtl": !!state.rtl_fm_present,
      "fm-lamp-audio": !!state.sounddevice_present,
    };
    Object.keys(lamps).forEach(function (id) {
      $(id).dataset.ok = lamps[id] ? "1" : "0";
    });

    var fixes = [];
    if (!state.rtl_fm_present) {
      fixes.push("The rtl-sdr tools aren't installed — <code>brew install librtlsdr</code>");
    }
    if (!state.sounddevice_present) {
      fixes.push("Audio output needs the live extra — <code>uv sync --extra dev --extra live</code>");
    }
    if (!devices.length) {
      fixes.push("No SDR detected — plug a dongle in and press rescan.");
    }
    var ready = fixes.length === 0;

    deviceNote.textContent = ready
      ? devices.map(function (d) { return d.name; }).join(" · ")
      : "no receiver";
    problems.hidden = ready;
    if (!ready) {
      problems.innerHTML = "<ul><li>" + fixes.join("</li><li>") + "</li></ul>";
    }

    ["fm-listen", "fm-save"].forEach(function (id) { $(id).disabled = !ready; });
    return ready;
  }

  function renderState(s) {
    state = Object.assign({}, state, s || {});
    var ready = renderDevice();
    if (state.running) {
      chassis.dataset.state = "on";
      pill.dataset.state = "live";
      pill.textContent = "● on air · " + Number(state.freq_mhz).toFixed(1);
      showFreq(state.freq_mhz);
      freq.value = state.freq_mhz;
      startPoll();
    } else {
      chassis.dataset.state = ready ? "idle" : "dead";
      pill.dataset.state = ready ? "off" : "warn";
      pill.textContent = ready ? "idle" : "no receiver";
      stopPoll();
    }
    renderFavs();
  }

  function startPoll() {
    if (poll) return;
    poll = setInterval(function () {
      get(cfg.controlUrl).then(function (s) {
        // Only react to the process dying; avoid fighting the user's dial.
        if (!s.running && state.running) {
          say("The receiver stopped." + (s.error ? " " + s.error : ""), "warn");
        }
        state = Object.assign({}, state, s);
        if (!s.running) { renderState(s); }
      });
    }, 2500);
  }

  function stopPoll() {
    if (poll) { clearInterval(poll); poll = null; }
  }

  /* ---- presets ----------------------------------------------------------- */
  function renderFavs() {
    favs.innerHTML = "";
    favsEmpty.hidden = stations.length > 0;
    var slots = Math.max(PRESET_SLOTS, stations.length);
    for (var i = 0; i < slots; i++) {
      var s = stations[i];
      var b = document.createElement("button");
      b.type = "button";
      if (!s) {
        b.className = "fm-preset is-empty";
        b.disabled = true;
        b.textContent = "—";
        favs.appendChild(b);
        continue;
      }
      b.className = "fm-preset";
      var mhz = Number(s.freq_mhz).toFixed(1);
      b.innerHTML =
        '<span class="n">P' + (i + 1) + "</span>" +
        '<span class="name"></span>' +
        '<span class="mhz">' + mhz + " MHz</span>";
      b.querySelector(".name").textContent = s.name === mhz ? "FM " + mhz : s.name;
      b.title = "Tune " + mhz + " MHz (shift-click to remove)";
      if (state.running && Math.abs(state.freq_mhz - s.freq_mhz) < 0.05) {
        b.setAttribute("aria-selected", "true");
      }
      b.addEventListener("click", makePresetHandler(s));
      favs.appendChild(b);
    }
  }

  function makePresetHandler(s) {
    return function (ev) {
      if (ev.shiftKey) { removeStation(s); return; }
      freq.value = s.freq_mhz;
      showFreq(s.freq_mhz);
      tune(s.freq_mhz);
    };
  }

  function loadStations() {
    return get(cfg.stationsUrl).then(function (d) {
      stations = d.stations || [];
      renderFavs();
    });
  }

  function removeStation(s) {
    if (!window.confirm("Remove " + s.name + "?")) return;
    post(cfg.stationsUrl, { id: s.id, delete: true }).then(function () {
      loadStations();
    });
  }

  function saveStation() {
    var mhz = Number(freq.value);
    var name = window.prompt("Name this preset", Number(mhz).toFixed(1));
    if (!name) return;
    post(cfg.stationsUrl, { name: name, freq_mhz: mhz }).then(function (r) {
      if (!r.ok) { say(r.error || "Couldn't save that preset.", "error"); return; }
      say("Preset saved.", "ok");
      loadStations().then(renderFavs);
    });
  }

  /* ---- tuning ------------------------------------------------------------ */
  function tune(mhz) {
    say("Tuning " + Number(mhz).toFixed(1) + "…");
    post(cfg.controlUrl, { action: "tune", freq_mhz: Number(mhz) }).then(function (r) {
      if (!r.ok) { say(r.error || "Couldn't tune.", "error"); return; }
      say("");
      renderState(r.data);
    });
  }

  function stop() {
    post(cfg.controlUrl, { action: "stop" }).then(function (r) {
      say("");
      renderState(r.data);
    });
  }

  /* ---- wiring ------------------------------------------------------------ */
  function nudge(delta) {
    freq.value = (Number(freq.value) + delta).toFixed(1);
    showFreq(freq.value);
    // Retune live if we're already playing — that's how a real tuner behaves.
    if (state.running) tune(freq.value);
  }

  freq.addEventListener("input", function () { showFreq(freq.value); });
  freq.addEventListener("change", function () { if (state.running) tune(freq.value); });
  $("fm-down").addEventListener("click", function () { nudge(-0.1); });
  $("fm-up").addEventListener("click", function () { nudge(0.1); });
  $("fm-listen").addEventListener("click", function () { tune(freq.value); });
  $("fm-stop").addEventListener("click", stop);
  $("fm-save").addEventListener("click", saveStation);
  $("fm-rescan").addEventListener("click", function () {
    deviceNote.textContent = "scanning…";
    get(cfg.controlUrl + "?refresh=1").then(renderState);
  });

  showFreq(freq.value);
  loadStations().then(function () {
    return get(cfg.controlUrl);
  }).then(renderState);
})();
