/* QSO form — the logbook line as a radio panel.
 *
 * Smart affordances: callsign uppercases as you type and looks up
 * worked-before history + QRZ on blur (filling empty station fields, never
 * overwriting what the operator typed), the band chip derives live from the
 * frequency, mode/RST chips are one-tap, and "now" stamps UTC. */
"use strict";

function initCWQSOForm(opts) {
  const form = document.getElementById("qso-form");
  const call = form.querySelector('[name="call"]');
  const status = document.getElementById("qso-lookup");
  const freq = form.querySelector('[name="freq_mhz"]');
  const bandChip = document.getElementById("qso-band");
  const mode = form.querySelector('[name="mode"]');
  const when = form.querySelector('[name="when"]');

  const BANDS = [
    [1.8, 2.0, "160m"], [3.5, 4.0, "80m"], [5.3, 5.41, "60m"], [7.0, 7.3, "40m"],
    [10.1, 10.15, "30m"], [14.0, 14.35, "20m"], [18.068, 18.168, "17m"],
    [21.0, 21.45, "15m"], [24.89, 24.99, "12m"], [28.0, 29.7, "10m"],
    [50.0, 54.0, "6m"], [144.0, 148.0, "2m"], [420.0, 450.0, "70cm"],
  ];

  // ── callsign: uppercase + lookup on blur ──────────────────────────────
  call.addEventListener("input", () => {
    const pos = call.selectionStart;
    call.value = call.value.toUpperCase();
    call.setSelectionRange(pos, pos);
  });

  function fillIfEmpty(name, value) {
    const input = form.querySelector('[name="' + name + '"]');
    if (input && !input.value && value) input.value = value;
  }

  let lastLooked = "";
  call.addEventListener("blur", () => {
    const value = call.value.trim();
    if (!value || value === lastLooked || !/^[A-Z]{1,2}\d[A-Z]{1,4}$/.test(value)) return;
    lastLooked = value;
    status.textContent = "looking up " + value + "…";
    fetch(opts.lookupUrl + "?call=" + encodeURIComponent(value), { credentials: "same-origin" })
      .then((r) => r.json())
      .then((j) => {
        const d = j.data || j;
        const src = d.last || d.qrz;
        if (src) {
          fillIfEmpty("name", src.name);
          fillIfEmpty("qth", src.qth);
          fillIfEmpty("gridsquare", src.gridsquare || src.grid);
          fillIfEmpty("country", src.country);
        }
        if (d.worked) {
          const lastWhen = d.last ? new Date(d.last.when) : null;
          status.textContent =
            "worked ×" + d.worked +
            (lastWhen ? " · last " + lastWhen.toISOString().slice(0, 10) : "") +
            (d.last && d.last.name ? " · " + d.last.name : "");
        } else if (d.qrz && d.qrz.name) {
          status.textContent = "new one · QRZ: " + d.qrz.name + (d.qrz.qth ? ", " + d.qrz.qth : "");
        } else {
          status.textContent = "new one — not in your log";
        }
      })
      .catch(() => { status.textContent = ""; });
  });

  // ── band derives from frequency, live ─────────────────────────────────
  function updateBand() {
    const mhz = parseFloat(freq.value);
    const hit = BANDS.find(([lo, hi]) => mhz >= lo && mhz <= hi);
    bandChip.style.display = hit ? "" : "none";
    if (hit) bandChip.textContent = hit[2];
  }
  freq.addEventListener("input", updateBand);
  updateBand();

  // ── chips ─────────────────────────────────────────────────────────────
  form.querySelectorAll(".cw-mode-chip").forEach((chip) => {
    chip.addEventListener("click", () => { mode.value = chip.dataset.mode; markMode(); });
  });
  function markMode() {
    form.querySelectorAll(".cw-mode-chip").forEach((c) =>
      c.classList.toggle("picked", c.dataset.mode === mode.value.toUpperCase()));
  }
  mode.addEventListener("input", markMode);
  markMode();

  form.querySelectorAll(".cw-rst-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const sent = form.querySelector('[name="rst_sent"]');
      const rcvd = form.querySelector('[name="rst_rcvd"]');
      sent.value = chip.dataset.rst;
      if (!rcvd.value) rcvd.value = chip.dataset.rst;
    });
  });

  // ── "now" in UTC ──────────────────────────────────────────────────────
  document.getElementById("qso-now").addEventListener("click", () => {
    when.value = new Date().toISOString().slice(0, 16);
  });

  // Enter in any single-line input submits (textarea keeps its newlines)
  form.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.tagName === "INPUT") {
      e.preventDefault();
      form.requestSubmit();
    }
  });
}
