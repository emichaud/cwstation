/* CW Monitor — paper-tape renderer.
 *
 * Renders sessions produced by the Python decoder (apps/cw/engine). This file
 * only DRAWS; all DSP and decoding happened server-side — single source of
 * truth. Colors are resolved from the active SmallStack palette at init (and
 * re-resolved on palette/theme change), so the tape matches every palette.
 *
 * Usage:
 *   const mon = initCWMonitor({
 *     canvas, decodedEl, truthEl, wpmEl, snrEl, toneEl, confEl,
 *     playBtn, restartBtn, rateInput, rateLabel, tabsEl,   // tabsEl optional
 *     sessions: {"name": sessionDict, ...},
 *   });
 */
"use strict";

function initCWMonitor(opts) {
  const cv = opts.canvas;
  const ctx = cv.getContext("2d");
  const el = opts;
  const sessions = opts.sessions;
  const names = Object.keys(sessions);
  if (!names.length) return null;

  const reduce = matchMedia("(prefers-reduced-motion:reduce)").matches;
  const WINDOW = 3.2, PLAYHEAD = 0.72; // seconds visible; playhead position

  let C = {};
  function resolveColors() {
    const css = getComputedStyle(document.documentElement);
    const v = (name, fallback) => (css.getPropertyValue(name).trim() || fallback);
    C = {
      signal: v("--primary", "#10b981"),
      line: v("--card-border", "#30363d"),
      muted: v("--body-quiet-color", "#8b949e"),
      faint: v("--text-muted", "#6e7681"),
      ink: v("--body-fg", "#e6edf3"),
      bad: v("--error-fg", "#f85149"),
      play: v("--success-fg", "#3fb950"),
    };
  }
  new MutationObserver(() => { resolveColors(); render(); }).observe(
    document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-palette"] });

  let S, dur, t = 0, playing = false, rate = 1, last = 0, dpr = 1;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = cv.clientWidth, h = cv.clientHeight;
    cv.width = w * dpr; cv.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener("resize", () => { resize(); draw(); });

  function load(i) {
    S = sessions[names[i]];
    dur = (S.env_t.length ? S.env_t[S.env_t.length - 1] : 0) + 0.6;
    if (el.truthEl) el.truthEl.textContent = S.meta.truth || S.meta.decoded;
    if (el.toneEl) el.toneEl.textContent = Math.round(S.meta.tone_hz);
    if (el.tabsEl) {
      [...el.tabsEl.children].forEach((b, k) => b.setAttribute("aria-selected", k === i));
    }
    seek(0);
    if (reduce) { t = dur; render(); } else { play(true); }
  }
  function seek(v) { t = Math.max(0, Math.min(v, dur)); render(); }

  function decodedUpTo(time) {
    let txt = "", wpm = S.meta.wpm_final, snr = "–", conf = "–";
    for (const c of S.chars) {
      if (c.t1 <= time) { txt += c.c; wpm = c.wpm; snr = c.snr; conf = Math.round(c.conf * 100); }
    }
    return { txt, wpm, snr, conf };
  }

  function escapeHtml(s) {
    return s.replace(/[&<>]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[m]));
  }

  function render() {
    draw();
    const d = decodedUpTo(t);
    el.decodedEl.innerHTML =
      escapeHtml(d.txt).replace(/�/g, '<span class="cw-unknown">▯</span>') +
      '<span class="cw-cursor"></span>';
    if (el.wpmEl) el.wpmEl.textContent = d.wpm ? (+d.wpm).toFixed(0) : "–";
    if (el.snrEl) el.snrEl.textContent = d.snr === "–" ? "–" : (+d.snr).toFixed(0);
    if (el.confEl) el.confEl.textContent = d.conf;
  }

  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function draw() {
    const w = cv.clientWidth, h = cv.clientHeight;
    ctx.clearRect(0, 0, w, h);
    const t0 = t - WINDOW * PLAYHEAD, t1 = t0 + WINDOW;
    const X = (tt) => ((tt - t0) / WINDOW) * w;
    const baseY = h * 0.62, tapeH = 46;

    // time grid (every 0.5s)
    ctx.strokeStyle = C.line; ctx.lineWidth = 1;
    ctx.font = "10px ui-monospace,monospace";
    for (let s = Math.ceil(t0 / 0.5) * 0.5; s < t1; s += 0.5) {
      const x = X(s);
      ctx.globalAlpha = 0.5;
      ctx.beginPath(); ctx.moveTo(x, h * 0.16); ctx.lineTo(x, h * 0.9); ctx.stroke();
      ctx.globalAlpha = 0.6; ctx.fillStyle = C.faint;
      ctx.fillText(s.toFixed(1) + "s", x + 3, h * 0.9 + 2);
    }
    ctx.globalAlpha = 1;

    // envelope trace (behind the tape)
    if (S.env_t.length) {
      ctx.beginPath();
      let started = false;
      for (let k = 0; k < S.env_t.length; k++) {
        const tt = S.env_t[k];
        if (tt < t0 - 0.1 || tt > t1 + 0.1) continue;
        const x = X(tt), y = baseY - S.env_mag[k] * tapeH * 1.3;
        started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), (started = true));
      }
      ctx.strokeStyle = C.signal; ctx.globalAlpha = 0.3; ctx.lineWidth = 1.2;
      ctx.stroke(); ctx.globalAlpha = 1;
    }

    // keyed tape: dit/dah bars — bright once read, dim ahead of the playhead
    for (const r of S.key_runs) {
      if (!r.on) continue;
      const a = r.t, b = r.t + r.ms / 1000;
      if (b < t0 || a > t1) continue;
      const x = X(a), wpx = Math.max(2, X(b) - X(a));
      const passed = b <= t;
      ctx.fillStyle = C.signal;
      ctx.globalAlpha = passed ? 1 : 0.35;
      ctx.shadowColor = passed ? C.signal : "transparent";
      ctx.shadowBlur = passed ? 10 : 0;
      roundRect(x, baseY - tapeH / 2, wpx, tapeH, 4);
      ctx.fill();
      ctx.shadowBlur = 0; ctx.globalAlpha = 1;
    }

    // decoded characters above their elements
    ctx.textAlign = "center";
    for (const c of S.chars) {
      if (c.c === " ") continue;
      const mid = (c.t0 + c.t1) / 2;
      if (mid < t0 || mid > t1) continue;
      const x = X(mid), resolved = c.t1 <= t;
      ctx.font = "600 20px ui-monospace,monospace";
      ctx.globalAlpha = resolved ? 1 : 0.25;
      ctx.fillStyle = c.c === "�" ? C.bad : resolved ? C.ink : C.faint;
      ctx.fillText(c.c === "�" ? "▯" : c.c, x, baseY - tapeH / 2 - 14);
      if (resolved) {
        ctx.globalAlpha = 0.55;
        ctx.font = "10px ui-monospace,monospace";
        ctx.fillStyle = C.muted;
        ctx.fillText(c.m, x, baseY - tapeH / 2 - 2);
      }
      ctx.globalAlpha = 1;
    }
    ctx.textAlign = "left";

    // playhead (now)
    const px = X(t);
    ctx.strokeStyle = C.play; ctx.lineWidth = 1.5; ctx.globalAlpha = 0.9;
    ctx.beginPath(); ctx.moveTo(px, h * 0.12); ctx.lineTo(px, h * 0.92); ctx.stroke();
    ctx.fillStyle = C.play;
    ctx.beginPath();
    ctx.moveTo(px - 5, h * 0.12); ctx.lineTo(px + 5, h * 0.12); ctx.lineTo(px, h * 0.12 + 7);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.font = "9px ui-monospace,monospace"; ctx.textAlign = "center";
    ctx.fillText("NOW", px, h * 0.1);
    ctx.textAlign = "left";
  }

  function play(on) {
    playing = on;
    if (el.playBtn) el.playBtn.textContent = on ? "❚❚" : "▶";
    if (on) { last = performance.now(); loop(); }
  }
  function loop() {
    if (!playing) return;
    const now = performance.now();
    t += ((now - last) / 1000) * rate; last = now;
    if (t >= dur) { t = dur; render(); play(false); return; }
    render();
    requestAnimationFrame(loop);
  }

  // build tabs
  if (el.tabsEl) {
    names.forEach((nm, i) => {
      const b = document.createElement("button");
      b.className = "cw-tab"; b.setAttribute("role", "tab");
      b.textContent = nm;
      b.onclick = () => load(i);
      el.tabsEl.appendChild(b);
    });
  }
  if (el.playBtn) el.playBtn.onclick = () => { if (t >= dur) seek(0); play(!playing); };
  if (el.restartBtn) el.restartBtn.onclick = () => { seek(0); play(true); };
  if (el.rateInput) el.rateInput.oninput = (e) => {
    rate = +e.target.value;
    if (el.rateLabel) el.rateLabel.textContent = rate.toFixed(1) + "×";
  };

  resolveColors();
  resize();
  load(0);
  return { load, seek, play };
}
