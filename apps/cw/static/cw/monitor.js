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
 *     playBtn, restartBtn, rateInput, rateLabel,
 *     tabsEl,        // optional: session tab strip
 *     audioBtn,      // optional: sidetone on/off toggle (WebAudio)
 *     audioStart,    // optional: start with sidetone enabled
 *     messageEl,     // optional: full-message readout that colors in as
 *                    //           characters complete (the Send view)
 *     sessions: {"name": sessionDict, ...},
 *   });
 */
"use strict";

function initCWMonitor(opts) {
  const cv = opts.canvas;
  const ctx = cv.getContext("2d");
  const el = opts;
  // Live mode: opts.live = { url, onStatus } — the session grows as batches
  // arrive over the WebSocket and the playhead follows the data edge.
  const live = opts.live || null;
  let sessions, names;
  if (live) {
    sessions = { live: {
      meta: { tone_hz: 0, wpm_final: 0, truth: "", decoded: "" },
      env_t: [], env_mag: [], key_runs: [], chars: [],
    } };
    names = ["live"];
  } else {
    sessions = opts.sessions;
    names = Object.keys(sessions);
    if (!names.length) return null;
  }
  const LIVE_LAG = 0.3; // seconds behind the newest data the playhead sits

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
  let msgSpans = [];

  // ── RF readout: rig dial + audio tone → the signal's actual frequency ─
  // CW/CWR rigs display signal RF on the dial directly; in SSB/data modes
  // the audio tone offsets the suppressed carrier.
  let dial = null; // {hz, mode, virtual}

  function signalRF() {
    if (!dial || !S) return null;
    const tone = (S.meta && S.meta.tone_hz) || 0;
    const mode = (dial.mode || "").toUpperCase();
    if (mode === "USB" || mode === "PKTUSB") return dial.hz + tone;
    if (mode === "LSB" || mode === "PKTLSB") return dial.hz - tone;
    return dial.hz; // CW/CWR: the dial already reads the signal's RF
  }

  function renderRF() {
    if (!el.rfEl) return;
    const rf = signalRF();
    if (el.rfWrapEl) el.rfWrapEl.style.display = rf ? "" : "none";
    if (rf) el.rfEl.textContent = (rf / 1e6).toFixed(4);
    if (el.rfLabelEl && dial) {
      el.rfLabelEl.textContent = dial.virtual ? "rf · virtual vfo" : "rf · " + (dial.mode || "");
    }
  }

  function setDial(hz, mode, virtual) {
    dial = hz ? { hz: hz, mode: mode, virtual: !!virtual } : null;
    renderRF();
  }

  // ── tutor mode: expand CW shorthand into plain English ────────────────
  // The dictionary (Python source of truth) is fetched once on first enable;
  // the panel shows only the abbreviations actually present in the copy.
  let tutorOn = false;
  let tutorDict = null;
  const MAX_GLOSS = 30;

  const SIGN_TOKENS = new Set(["+", "=", "(", ")", "&"]);
  function cleanToken(raw) {
    if (SIGN_TOKENS.has(raw)) return raw;
    // strip trailing punctuation but keep <> and () — they carry meaning
    return raw.replace(/^[.,?!;:/]+|[.,?!;:/]+$/g, "").toUpperCase();
  }

  function renderGloss() {
    if (!el.glossChipsEl) return;
    if (!tutorOn || !tutorDict) return;
    const text = decodedUpTo(t).txt;
    const seen = new Set();
    const hits = [];
    for (const raw of text.split(/\s+/)) {
      const tok = cleanToken(raw);
      if (tok && tutorDict[tok] && !seen.has(tok)) {
        seen.add(tok);
        hits.push(tok);
      }
    }
    const shown = hits.slice(-MAX_GLOSS);
    el.glossChipsEl.innerHTML = "";
    if (!shown.length) {
      el.glossChipsEl.innerHTML = '<span class="cw-gloss-empty">watching for shorthand…</span>';
      return;
    }
    for (const tok of shown) {
      const chip = document.createElement("span");
      chip.className = "cw-gloss-chip";
      const b = document.createElement("b");
      b.textContent = tok;
      chip.appendChild(b);
      chip.appendChild(document.createTextNode(tutorDict[tok]));
      el.glossChipsEl.appendChild(chip);
    }
  }

  function tutorToggle() {
    tutorOn = !tutorOn;
    if (el.tutorBtn) el.tutorBtn.setAttribute("aria-pressed", String(tutorOn));
    if (el.glossEl) el.glossEl.classList.toggle("open", tutorOn);
    if (tutorOn && !tutorDict) {
      fetch("/cw/abbrev/", { credentials: "same-origin" })
        .then((r) => r.json())
        .then((j) => { tutorDict = (j.data || j).lookup || {}; renderGloss(); })
        .catch(() => { tutorDict = {}; });
    } else {
      renderGloss();
    }
  }

  // ── sidetone (WebAudio) ───────────────────────────────────────────────
  // The keying data comes from the decoder's key runs; this just sounds it.
  let audioOn = !!opts.audioStart, actx = null, oscGain = null, osc = null;
  const SIDETONE_LEVEL = 0.22;

  function audioInit() {
    if (actx) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) { audioOn = false; return; }
    actx = new Ctx();
    osc = actx.createOscillator();
    osc.type = "sine";
    oscGain = actx.createGain();
    oscGain.gain.value = 0;
    osc.connect(oscGain).connect(actx.destination);
    osc.start();
  }

  function audioSync() {
    // (Re)schedule the gain envelope from the current playhead. Called on
    // every play/pause/seek/rate/session change so the schedule always
    // matches the tape.
    if (!actx || !oscGain) return;
    if (actx.state === "suspended") actx.resume();
    const g = oscGain.gain, now = actx.currentTime;
    g.cancelScheduledValues(now);
    g.setValueAtTime(0, now);
    if (!audioOn || !playing) return;
    osc.frequency.setValueAtTime(S.meta.tone_hz || 600, now);
    for (const r of S.key_runs) {
      if (!r.on) continue;
      const a = r.t, b = r.t + r.ms / 1000;
      if (b <= t) continue;
      const start = now + Math.max(0, (a - t) / rate);
      const end = now + (b - t) / rate;
      if (end - start < 0.012) continue;
      g.setValueAtTime(0, start);
      g.linearRampToValueAtTime(SIDETONE_LEVEL, start + 0.005); // click-free edges
      g.setValueAtTime(SIDETONE_LEVEL, end - 0.005);
      g.linearRampToValueAtTime(0, end);
    }
  }

  function audioToggle() {
    audioOn = !audioOn;
    if (audioOn) audioInit(); // user gesture — safe to create the context here
    audioSync();
    updateAudioBtn();
  }

  function updateAudioBtn() {
    if (!el.audioBtn) return;
    el.audioBtn.setAttribute("aria-pressed", String(audioOn));
    el.audioBtn.textContent = audioOn ? "🔊" : "🔇";
  }

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
    if (el.messageEl) buildMessage();
    renderRF();
    seek(0);
    if (reduce) { t = dur; render(); } else { play(true); }
  }
  function seek(v) { t = Math.max(0, Math.min(v, dur)); audioSync(); render(); }

  function buildMessage() {
    el.messageEl.textContent = "";
    msgSpans = S.chars.map((c) => {
      const sp = document.createElement("span");
      sp.textContent = c.c === "�" ? "▯" : c.c;
      sp.className = "cw-msg-pending";
      el.messageEl.appendChild(sp);
      return { sp, c };
    });
  }

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

  // The decode window sticks to its newest text unless the operator has
  // scrolled up to read older copy.
  let decodedPinned = true;
  if (el.decodedEl) {
    el.decodedEl.addEventListener("scroll", () => {
      const d = el.decodedEl;
      decodedPinned = d.scrollTop + d.clientHeight >= d.scrollHeight - 8;
    });
  }

  function render() {
    draw();
    const d = decodedUpTo(t);
    if (el.decodedEl) {
      el.decodedEl.innerHTML =
        escapeHtml(d.txt).replace(/�/g, '<span class="cw-unknown">▯</span>') +
        '<span class="cw-cursor"></span>';
      if (decodedPinned) el.decodedEl.scrollTop = el.decodedEl.scrollHeight;
    }
    for (const { sp, c } of msgSpans) {
      sp.className =
        c.t1 <= t
          ? (c.c === "�" ? "cw-msg-bad" : "cw-msg-done")
          : c.t0 <= t
            ? "cw-msg-active"
            : "cw-msg-pending";
    }
    if (el.wpmEl) el.wpmEl.textContent = d.wpm ? (+d.wpm).toFixed(0) : "–";
    if (el.snrEl) el.snrEl.textContent = d.snr === "–" ? "–" : (+d.snr).toFixed(0);
    if (el.confEl) el.confEl.textContent = d.conf;
    renderGloss();
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

    // text-mode traffic (fldigi taps): no key runs — draw a continuous
    // carrier ribbon with a tick at each character commit (varicode
    // character boundaries are real decoder events; the ribbon between
    // them is the mode's continuous carrier)
    const textChars = S.chars.filter((c) => c.m === "" && c.c !== " ");
    if (textChars.length) {
      const ribbonH = 12;
      let spanStart = null, prevEnd = null;
      const spans = [];
      for (const c of textChars) {
        if (spanStart === null) { spanStart = c.t0; }
        else if (c.t0 - prevEnd > 2.0) { spans.push([spanStart, prevEnd]); spanStart = c.t0; }
        prevEnd = c.t1;
      }
      if (spanStart !== null) spans.push([spanStart, prevEnd]);
      for (const [a0, b0] of spans) {
        const a = Math.max(a0 - 0.15, t0), b = Math.min(b0 + 0.15, t1);
        if (b < t0 || a > t1) continue;
        const passed = b0 <= t;
        ctx.fillStyle = C.signal;
        ctx.globalAlpha = passed ? 0.75 : 0.3;
        ctx.fillRect(X(a), baseY - ribbonH / 2, Math.max(2, X(b) - X(a)), ribbonH);
      }
      for (const c of textChars) {
        if (c.t1 < t0 || c.t1 > t1) continue;
        const passed = c.t1 <= t;
        ctx.fillStyle = C.signal;
        ctx.globalAlpha = passed ? 1 : 0.35;
        ctx.shadowColor = passed ? C.signal : "transparent";
        ctx.shadowBlur = passed ? 8 : 0;
        ctx.fillRect(X(c.t1) - 1, baseY - 14, 2, 28);
        ctx.shadowBlur = 0;
      }
      ctx.globalAlpha = 1;
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
    if (audioOn && on) audioInit(); // play click is a user gesture
    audioSync();
    if (on) { last = performance.now(); loop(); }
  }
  function loop() {
    if (!playing) return;
    const now = performance.now();
    t += ((now - last) / 1000) * rate; last = now;
    if (live) {
      // Follow the incoming data: never pass the edge, and jump forward if
      // we fell far behind (backgrounded tab).
      const edge = Math.max(0, dur - LIVE_LAG);
      if (t > edge || edge - t > 3) t = edge;
      render();
      requestAnimationFrame(loop);
      return;
    }
    if (t >= dur) { t = dur; render(); play(false); return; }
    render();
    requestAnimationFrame(loop);
  }

  // ── live feed ─────────────────────────────────────────────────────────
  function liveStatus(state) {
    if (live && live.onStatus) {
      live.onStatus(state, { source: feeds.source, conflict: feeds.conflict });
    }
  }

  const heardCalls = new Set();
  function liveHeard(calls) {
    // "Heard on the band" chips — click one to reply (the responder).
    if (!live.callsEl) return;
    for (const call of calls) {
      if (heardCalls.has(call)) continue;
      heardCalls.add(call);
      const chip = document.createElement("a");
      chip.className = "cw-heard-chip";
      chip.href = live.sendUrl + "?to=" + encodeURIComponent(call);
      chip.innerHTML = "";
      chip.appendChild(document.createTextNode(call));
      const tag = document.createElement("span");
      tag.textContent = "reply";
      chip.appendChild(tag);
      if (live.onReply) {
        // open the send sheet in place instead of navigating (the href
        // remains for middle-click / open-in-new-tab)
        chip.addEventListener("click", (e) => {
          e.preventDefault();
          live.onReply(call);
        });
      }
      live.callsEl.appendChild(chip);
    }
    if (live.callsEmptyEl && heardCalls.size) live.callsEmptyEl.style.display = "none";
  }

  // feed identity: which command is driving this tape, and is a second one
  // interleaving with it? (two streamers on one tape scramble the copy)
  const feeds = { current: null, source: "", lastSeen: 0, conflict: false };

  function trackFeed(meta) {
    if (!meta || !meta.feed) return;
    const now = performance.now();
    if (feeds.current && meta.feed !== feeds.current && now - feeds.lastSeen < 6000) {
      feeds.conflict = true;  // two live feeds alternating within seconds
    } else if (meta.feed !== feeds.current) {
      feeds.conflict = false; // clean handover to a new feed
    }
    feeds.current = meta.feed;
    feeds.source = meta.source || "";
    feeds.lastSeen = now;
    liveStatus("live");
  }

  function liveMerge(b) {
    if (b.meta) {
      trackFeed(b.meta);
      if (b.meta.tone_hz && el.toneEl) el.toneEl.textContent = Math.round(b.meta.tone_hz);
      if (b.meta.tone_hz) { S.meta.tone_hz = b.meta.tone_hz; renderRF(); }
      if (b.meta.calls) liveHeard(b.meta.calls);
    }
    if (b.env_t && b.env_t.length) { S.env_t.push(...b.env_t); S.env_mag.push(...b.env_mag); }
    if (b.key_runs && b.key_runs.length) S.key_runs.push(...b.key_runs);
    if (b.chars && b.chars.length) S.chars.push(...b.chars);
    // text-only taps (fldigi) carry no envelope — follow char time instead
    const envEdge = S.env_t.length ? S.env_t[S.env_t.length - 1] : 0;
    const charEdge = S.chars.length ? S.chars[S.chars.length - 1].t1 : 0;
    dur = Math.max(envEdge, charEdge) + 0.6;
    if (playing && audioOn) audioSync();
    if (!playing) render();
  }

  function liveConnect() {
    liveStatus("connecting");
    let ws;
    try { ws = new WebSocket(live.url); } catch (e) { liveStatus("off"); return; }
    ws.onopen = () => liveStatus("live");
    ws.onmessage = (e) => liveMerge(JSON.parse(e.data));
    ws.onerror = () => ws.close();
    ws.onclose = () => { liveStatus("off"); setTimeout(liveConnect, 2500); };
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
  if (el.audioBtn) { el.audioBtn.onclick = audioToggle; updateAudioBtn(); }
  if (el.tutorBtn) el.tutorBtn.onclick = tutorToggle;
  if (el.rateInput) el.rateInput.oninput = (e) => {
    rate = +e.target.value;
    if (el.rateLabel) el.rateLabel.textContent = rate.toFixed(1) + "×";
    audioSync();
  };

  resolveColors();
  resize();
  load(0);
  if (live) { liveConnect(); play(true); }
  renderRF();
  return { load, seek, play, setDial };
}
