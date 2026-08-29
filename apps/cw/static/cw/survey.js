/* Antenna survey — run a sweep, then compare runs.
 *
 * Python measures; this picks bands, polls progress, and renders. The
 * comparison matrix is the point of the page: bands down the side, one column
 * per saved antenna, best figure in each row highlighted.
 */
(function () {
  "use strict";
  var cfg = window.CW_SURVEY || {};
  if (!cfg.url) return;

  var $ = function (id) { return document.getElementById(id); };
  var pill = $("sv-pill");
  var statusEl = $("sv-status");
  var bandsEl = $("sv-bands");
  var gain = $("sv-gain");

  var bands = [];
  var devices = [];
  var surveys = [];
  var selected = [];
  var poll = null;
  var lastSaved = true;
  var SECONDS_PER_BAND = 2.4;  // measured; keeps the estimate honest

  function get(query) {
    return fetch(cfg.url + (query || ""), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) { return j.data || j; });
  }

  function post(body) {
    return fetch(cfg.url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().then(function (j) {
        var msg = (j.errors && j.errors.__all__ && j.errors.__all__[0]) || j.error;
        return { ok: r.ok, data: j.data || j, error: msg };
      });
    });
  }

  function say(msg, kind) {
    statusEl.textContent = msg || "";
    statusEl.dataset.kind = kind || "";
  }

  /* strength class: matches bandscan.verdict()'s thresholds */
  function strength(snr) {
    if (snr === null || snr === undefined) return 0;
    if (snr < 3) return 1;
    if (snr < 10) return 2;
    return 3;
  }
  function verdict(snr) {
    if (snr === null || snr === undefined) return "no data";
    if (snr < 3) return "nothing heard";
    if (snr < 10) return "faint";
    if (snr < 20) return "workable";
    return "strong";
  }

  /* ---- band picker -------------------------------------------------------- */
  function renderBands() {
    bandsEl.innerHTML = "";
    bands.forEach(function (b) {
      var label = document.createElement("label");
      label.className = "sv-band";
      label.dataset.checked = selected.indexOf(b.key) >= 0 ? "true" : "false";
      label.dataset.hf = b.hf ? "true" : "false";

      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selected.indexOf(b.key) >= 0;
      cb.addEventListener("change", function () {
        var i = selected.indexOf(b.key);
        if (cb.checked && i < 0) selected.push(b.key);
        if (!cb.checked && i >= 0) selected.splice(i, 1);
        label.dataset.checked = cb.checked ? "true" : "false";
        updateEstimate();
      });

      var text = document.createElement("div");
      var title = document.createElement("b");
      title.textContent = b.label + "  " + b.low_mhz + "–" + b.high_mhz + " MHz";
      if (b.reference) {
        var tag = document.createElement("span");
        tag.className = "sv-ref-tag";
        tag.textContent = "always on";
        title.appendChild(tag);
      }
      var note = document.createElement("span");
      note.textContent = b.note || "";
      text.appendChild(title);
      if (b.note) text.appendChild(note);

      label.appendChild(cb);
      label.appendChild(text);
      bandsEl.appendChild(label);
    });
    updateEstimate();
  }

  /* Which stick, what it can do. Recorded with a saved run because a survey
     from another dongle isn't comparable to this one. */
  function renderDevices() {
    var sel = $("sv-device");
    var previous = sel.value;
    sel.innerHTML = "";
    if (!devices.length) {
      var none = document.createElement("option");
      none.textContent = "no SDR detected";
      sel.appendChild(none);
      sel.disabled = true;
      $("sv-device-note").textContent =
        "Plug a dongle in and press rescan. Only RTL-SDR sticks are detected — " +
        "SDRplay, Airspy and HackRF speak a different driver.";
      $("sv-ds-row").dataset.unsupported = "true";
      return;
    }
    sel.disabled = devices.length === 1;
    devices.forEach(function (d) {
      var opt = document.createElement("option");
      opt.value = String(d.index);
      opt.textContent = "#" + d.index + "  " + d.name;
      sel.appendChild(opt);
    });
    if (previous) sel.value = previous;

    var current = currentDevice();
    var canHf = !!(current && current.direct_sampling);
    var bits = [];
    if (current && current.tuner) bits.push(current.tuner + " tuner");
    if (current && (current.gains || []).length) {
      bits.push((current.gains || []).length + " gain steps");
    }
    bits.push(canHf
      ? "known to support direct sampling — HF bands are reachable"
      : "no known ADC tap: HF needs an upconverter, or a stick like the RTL-SDR Blog V3");
    $("sv-device-note").textContent = bits.join(" · ");
    $("sv-ds-row").dataset.unsupported = canHf ? "false" : "true";
    applyGainSteps(current);
  }

  function currentDevice() {
    var idx = Number($("sv-device").value);
    return devices.filter(function (d) { return d.index === idx; })[0] || devices[0];
  }

  /* Drive the gain control from the tuner's real steps — rtl_power snaps to the
     nearest anyway, so offering arbitrary values would record a gain that was
     never used. */
  function applyGainSteps(device) {
    var steps = (device && device.gains) || [];
    if (!steps.length) return;
    gain.min = 0;
    gain.max = String(steps.length - 1);
    gain.step = 1;
    gain.dataset.steps = JSON.stringify(steps);
    var target = steps.reduce(function (best, g, i) {
      return Math.abs(g - 40) < Math.abs(steps[best] - 40) ? i : best;
    }, 0);
    gain.value = String(target);
    showGain();
  }

  function gainValue() {
    var steps = gain.dataset.steps ? JSON.parse(gain.dataset.steps) : null;
    return steps ? steps[Number(gain.value)] : Number(gain.value);
  }

  function showGain() {
    $("sv-gain-val").textContent = Number(gainValue()).toFixed(1) + " dB";
  }

  function updateEstimate() {
    var secs = Math.round(selected.length * SECONDS_PER_BAND);
    $("sv-est").textContent = selected.length ? "~" + secs + " s" : "pick a band";
  }

  /* ---- results ------------------------------------------------------------ */
  function renderRows(container, results) {
    container.innerHTML = "";
    results.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "sv-row";

      var name = document.createElement("div");
      name.className = "sv-name";
      var strong = document.createElement("strong");
      strong.textContent = r.label;
      name.appendChild(strong);
      if (r.reference) {
        var tag = document.createElement("span");
        tag.className = "sv-ref-tag";
        tag.textContent = "always on";
        name.appendChild(tag);
      }
      var sub = document.createElement("i");
      sub.textContent = r.snr_db === null
        ? "no data"
        : verdict(r.snr_db) + " · floor " + r.floor_db + " dB"
          + (r.peak_mhz ? " · peak " + r.peak_mhz + " MHz" : "");
      name.appendChild(sub);

      var db = document.createElement("div");
      var cls = "sv-s" + strength(r.snr_db);
      db.className = "sv-db " + cls;
      db.textContent = r.snr_db === null ? "—" : r.snr_db.toFixed(1);

      var meter = document.createElement("div");
      meter.className = "sv-meter";
      var fill = document.createElement("i");
      fill.className = cls;
      // 30 dB is a full bar — a strong local FM signal sits around there
      var pct = Math.max(0, Math.min(100, ((r.snr_db || 0) / 30) * 100));
      fill.style.width = pct + "%";
      meter.appendChild(fill);

      row.appendChild(name);
      row.appendChild(db);
      row.appendChild(meter);
      container.appendChild(row);
    });
  }

  /* ---- comparison matrix -------------------------------------------------- */
  function renderMatrix() {
    var card = $("sv-compare-card");
    if (surveys.length < 1) { card.hidden = true; return; }
    card.hidden = false;

    // rows: every band any survey covered, in the canonical band order
    var keys = [];
    bands.forEach(function (b) {
      if (surveys.some(function (s) {
        return (s.results || []).some(function (r) { return r.key === b.key; });
      })) keys.push(b);
    });

    var gains = surveys.map(function (s) { return s.gain_db; });
    var mixedGain = gains.some(function (g) { return Math.abs(g - gains[0]) > 0.05; });
    // Runs from two different dongles measure different hardware; comparing
    // them says nothing about the antennas.
    var devs = surveys.map(function (s) { return s.device || ""; });
    var mixedDevice = devs.some(function (d) { return d !== devs[0]; });

    var table = $("sv-matrix");
    table.innerHTML = "";
    var thead = document.createElement("thead");
    var hr = document.createElement("tr");
    hr.appendChild(document.createElement("th")).textContent = "Band";
    surveys.forEach(function (s) {
      var th = document.createElement("th");
      th.textContent = s.antenna;
      var small = document.createElement("small");
      small.textContent = new Date(s.created_at).toLocaleDateString() +
        " · " + s.gain_db + " dB" + (mixedDevice && s.device ? " · " + s.device : "");
      if (mixedGain || mixedDevice) small.className = "sv-gain-warn";
      th.appendChild(small);
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    keys.forEach(function (b) {
      var tr = document.createElement("tr");
      var td0 = document.createElement("td");
      td0.textContent = b.label;
      if (b.reference) {
        var tag = document.createElement("span");
        tag.className = "sv-ref-tag";
        tag.textContent = "always on";
        td0.appendChild(tag);
      }
      tr.appendChild(td0);

      var vals = surveys.map(function (s) {
        var row = (s.results || []).find(function (r) { return r.key === b.key; });
        return row && row.snr_db !== null && row.snr_db !== undefined ? row.snr_db : null;
      });
      var best = Math.max.apply(null, vals.filter(function (v) { return v !== null; }));

      vals.forEach(function (v) {
        var td = document.createElement("td");
        if (v === null) { td.textContent = "—"; td.className = "sv-none"; }
        else {
          td.textContent = v.toFixed(1);
          // only crown a winner when someone actually heard something
          if (vals.filter(function (x) { return x !== null; }).length > 1
              && v === best && best >= 3) td.className = "sv-best";
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    if (mixedDevice) {
      say("Runs were taken on different SDRs — those columns measure different "
          + "receivers, not different antennas.", "warn");
    } else if (mixedGain) {
      say("Runs were taken at different gains — those columns aren't directly comparable.", "warn");
    }
  }

  function renderRuns() {
    var card = $("sv-runs-card");
    card.hidden = surveys.length === 0;
    var box = $("sv-runs");
    box.innerHTML = "";
    surveys.forEach(function (s) {
      var row = document.createElement("div");
      row.className = "sv-run";
      var name = document.createElement("b");
      name.textContent = s.antenna;
      var when = document.createElement("span");
      when.className = "sv-when";
      when.textContent = new Date(s.created_at).toLocaleString() +
        " · " + (s.results || []).length + " bands · gain " + s.gain_db + " dB";
      var spacer = document.createElement("span");
      spacer.className = "sv-spacer";
      var del = document.createElement("button");
      del.type = "button";
      del.textContent = "remove";
      del.setAttribute("aria-label", "Remove the " + s.antenna + " survey");
      del.addEventListener("click", function () {
        post({ action: "delete", id: s.id }).then(refresh);
      });
      row.appendChild(name); row.appendChild(when);
      row.appendChild(spacer); row.appendChild(del);
      box.appendChild(row);
    });
  }

  /* ---- run + poll --------------------------------------------------------- */
  function applyScan(scan) {
    var running = !!scan.running;
    $("sv-run").disabled = running;
    $("sv-progress").hidden = !running;
    pill.dataset.state = running ? "live" : "off";
    pill.textContent = running ? "● scanning" : "idle";
    if (running) {
      var pct = scan.total ? (scan.done / scan.total) * 100 : 0;
      $("sv-bar-fill").style.width = pct + "%";
      $("sv-progress-text").textContent =
        scan.current + " — " + scan.done + " of " + scan.total;
      startPoll();
    } else {
      stopPoll();
      if (scan.error) say(scan.error, "error");
    }
    if ((scan.results || []).length) {
      $("sv-latest-card").hidden = false;
      var saving = scan.saving !== false;
      $("sv-latest-title").textContent = saving
        ? (scan.antenna ? "Latest run — " + scan.antenna : "Latest run")
        : "Instant check";
      $("sv-latest-note").hidden = saving;
      renderRows($("sv-latest"), scan.results);
    }
  }

  function startPoll() {
    if (poll) return;
    poll = setInterval(function () {
      get().then(function (d) {
        applyScan(d.scan || {});
        if (!(d.scan || {}).running) {
          surveys = d.surveys || [];
          renderMatrix(); renderRuns();
          if (!(d.scan || {}).error) {
            say(lastSaved ? "Survey saved." : "Instant check done — not saved.", "ok");
          }
        }
      });
    }, 1200);
  }

  function stopPoll() { if (poll) { clearInterval(poll); poll = null; } }

  function refresh() {
    return get().then(function (d) {
      bands = d.bands || [];
      devices = d.devices || [];
      surveys = d.surveys || [];
      renderDevices();
      if (!selected.length) selected = ((d.defaults || {}).bands || []).slice();
      renderBands(); renderMatrix(); renderRuns();
      applyScan(d.scan || {});
    });
  }

  gain.addEventListener("input", showGain);
  $("sv-device").addEventListener("change", renderDevices);
  $("sv-rescan").addEventListener("click", function () {
    $("sv-device-note").textContent = "scanning…";
    get("?refresh=1").then(function (d) {
      devices = d.devices || [];
      renderDevices();
    });
  });

  function launch(body, label) {
    lastSaved = body.save !== false;
    say(label);
    post(body).then(function (r) {
      if (!r.ok) { say(r.error || "Couldn't start the sweep.", "error"); return; }
      applyScan(r.data.scan || {});
    });
  }

  $("sv-run").addEventListener("click", function () {
    var antenna = $("sv-antenna").value.trim();
    if (!antenna) { say("Name the antenna first — that's what saved runs are compared by.", "error"); return; }
    if (!selected.length) { say("Pick at least one band.", "error"); return; }
    launch({
      action: "start", antenna: antenna, save: true,
      bands: selected, gain_db: gainValue(),
      device_index: Number($("sv-device").value || 0),
      direct_sampling: $("sv-ds").checked,
    }, "Sweeping…");
  });

  // Instant check: the always-on bands only. They're the ones whose reading
  // actually tracks the antenna, and limiting to them keeps it quick.
  $("sv-quick").addEventListener("click", function () {
    var refs = bands.filter(function (b) { return b.reference; })
                    .map(function (b) { return b.key; });
    launch({
      action: "start", save: false,
      bands: refs.length ? refs : selected, gain_db: gainValue(),
      device_index: Number($("sv-device").value || 0),
      direct_sampling: $("sv-ds").checked,
    }, "Checking…");
  });

  refresh();
})();
