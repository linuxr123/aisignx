/* Minimal Dark — clean white time on black background */
window.renderClock = function (ctx) {
  const root = ctx.root;
  if (!root._init) {
    document.body.style.background = '#000';
    document.body.style.color = '#fff';
    root.innerHTML =
      '<div style="position:absolute;inset:0;display:flex;flex-direction:column;' +
      'align-items:center;justify-content:center;font-family:system-ui,-apple-system,sans-serif">' +
      '<div id="t" style="font-size:18vmin;font-weight:700;letter-spacing:-0.03em;line-height:1"></div>' +
      '<div id="d" style="font-size:4vmin;opacity:0.6;font-weight:300;margin-top:2vmin"></div>' +
      '</div>';
    root._init = true;
  }
  document.getElementById('t').textContent = ctx.timeStr;
  document.getElementById('d').textContent = ctx.showDate ? ctx.dateStr : '';
};
