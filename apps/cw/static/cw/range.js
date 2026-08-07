/* Paints the gold "filled" portion of custom range sliders.
 *
 * Webkit has no ::progress pseudo-element, so the filled track is a CSS
 * gradient stopped at --cw-range-fill; this keeps that variable current.
 * One delegated listener covers every slider, including ones revealed later
 * (the send sheet). Idempotent — safe to include on more than one partial. */
(function () {
  if (window.__cwRangeInit) return;
  window.__cwRangeInit = true;

  function paint(r) {
    const min = parseFloat(r.min) || 0;
    const max = parseFloat(r.max);
    const hi = isNaN(max) ? 100 : max;
    const val = parseFloat(r.value) || 0;
    const pct = hi > min ? ((val - min) / (hi - min)) * 100 : 0;
    r.style.setProperty("--cw-range-fill", pct + "%");
  }

  function scan() {
    document.querySelectorAll('input[type="range"]').forEach(paint);
  }

  document.addEventListener("input", function (e) {
    if (e.target && e.target.matches && e.target.matches('input[type="range"]')) {
      paint(e.target);
    }
  });

  if (document.readyState !== "loading") scan();
  else document.addEventListener("DOMContentLoaded", scan);
  setTimeout(scan, 400); // catch sliders shown after first paint
})();
