/* FM Radio page — tune rtl_fm, and keep a strip of saved stations.
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
  var pill = $("fm-pill");
  var freq = $("fm-freq");
  var freqVal = $("fm-freq-val");
  var status = $("fm-status");
  var favs = $("fm-favs");
  var favsEmpty = $("fm-favs-empty");
  var deviceNote = $("fm-device-note");
  var deviceDetail = $("fm-device-detail");

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
    freqVal.textContent = Number(mhz).toFixed(1) + " MHz";
  }

  /* ---- receiver presence -------------------------------------------------
   * Three independent failures, each with its own fix, so name them apart
   * rather than collapsing into one "unavailable".
   */
  function renderDevice() {
    var lines = [];
    var ready = true;
    if (!state.rtl_fm_present) {
      lines.push("The rtl-sdr tools aren't installed — <code>brew install librtlsdr</code>");
      ready = false;
    }
    if (!state.sounddevice_present) {
      lines.push("Audio output needs the live extra — <code>uv sync --extra dev --extra live</code>");
      ready = false;
    }
    var devices = state.devices || [];
    if (!devices.length) {
      lines.push("No SDR detected — plug a dongle in and press Rescan.");
      ready = false;
    }

    if (ready) {
      deviceNote.textContent = "Ready.";
      deviceDetail.hidden = false;
      deviceDetail.textContent = devices
        .map(function (d) { return "#" + d.index + "  " + d.name; })
        .join("\n");
    } else {
      deviceNote.textContent = "This machine can't play radio yet:";
      deviceDetail.hidden = false;
      deviceDetail.innerHTML = "<ul style=\"margin:0;padding-left:1.1rem\"><li>" +
        lines.join("</li><li>") + "</li></ul>";
    }

    ["fm-listen", "fm-save"].forEach(function (id) { $(id).disabled = !ready; });
    return ready;
  }

  function renderState(s) {
    state = Object.assign({}, state, s || {});
    var ready = renderDevice();
    if (state.running) {
      pill.dataset.state = "live";
      pill.textContent = "● on air · " + Number(state.freq_mhz).toFixed(1);
      showFreq(state.freq_mhz);
      freq.value = state.freq_mhz;
      startPoll();
    } else {
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
        // Only react to the process dying; avoid fighting the user's slider.
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

  /* ---- favourites strip -------------------------------------------------- */
  function renderFavs() {
    favs.innerHTML = "";
    favsEmpty.hidden = stations.length > 0;
    stations.forEach(function (s) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "cw-tab";
      // Default-named stations are just the frequency; don't print it twice.
      var mhz = Number(s.freq_mhz).toFixed(1);
      b.textContent = s.name === mhz ? mhz : s.name + " · " + mhz;
      b.title = "Tune " + s.freq_mhz + " MHz (shift-click to remove)";
      if (state.running && Math.abs(state.freq_mhz - s.freq_mhz) < 0.05) {
        b.setAttribute("aria-selected", "true");
      }
      b.addEventListener("click", function (ev) {
        if (ev.shiftKey) { removeStation(s); return; }
        freq.value = s.freq_mhz;
        showFreq(s.freq_mhz);
        tune(s.freq_mhz);
      });
      favs.appendChild(b);
    });
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
    var name = window.prompt("Name this station", Number(mhz).toFixed(1));
    if (!name) return;
    post(cfg.stationsUrl, { name: name, freq_mhz: mhz }).then(function (r) {
      if (!r.ok) { say(r.error || "Couldn't save that station.", "error"); return; }
      say("Saved " + name + ".", "ok");
      loadStations();
    });
  }

  /* ---- tuning ------------------------------------------------------------ */
  function tune(mhz) {
    say("Tuning " + Number(mhz).toFixed(1) + "…");
    post(cfg.controlUrl, { action: "tune", freq_mhz: Number(mhz) }).then(function (r) {
      if (!r.ok) { say(r.error || "Couldn't tune.", "error"); return; }
      say("Playing " + Number(mhz).toFixed(1) + " MHz.", "ok");
      renderState(r.data);
    });
  }

  function stop() {
    post(cfg.controlUrl, { action: "stop" }).then(function (r) {
      say("Stopped.");
      renderState(r.data);
    });
  }

  /* ---- wiring ------------------------------------------------------------ */
  freq.addEventListener("input", function () { showFreq(freq.value); });
  $("fm-down").addEventListener("click", function () {
    freq.value = (Number(freq.value) - 0.1).toFixed(1); showFreq(freq.value);
  });
  $("fm-up").addEventListener("click", function () {
    freq.value = (Number(freq.value) + 0.1).toFixed(1); showFreq(freq.value);
  });
  $("fm-listen").addEventListener("click", function () { tune(freq.value); });
  $("fm-stop").addEventListener("click", stop);
  $("fm-save").addEventListener("click", saveStation);
  $("fm-rescan").addEventListener("click", function () {
    deviceNote.textContent = "Scanning…";
    get(cfg.controlUrl + "?refresh=1").then(renderState);
  });

  showFreq(freq.value);
  loadStations().then(function () {
    return get(cfg.controlUrl);
  }).then(renderState);
})();
