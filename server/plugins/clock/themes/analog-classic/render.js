/* Analog Classic - SVG analog wall clock with hour, minute and second hands */
window.renderClock = function (ctx) {
  var root = ctx.root;
  if (!root._init) {
    document.body.style.background = "#0a0a0a";
    document.body.style.color = "#fff";

    // Build SVG once
    var ticks = "";
    for (var i = 0; i < 60; i++) {
      var angle = i * 6;
      var isHour = (i % 5 === 0);
      var len = isHour ? 7 : 3;
      var w = isHour ? 1.6 : 0.8;
      ticks += '<line x1="50" y1="' + (5) + '" x2="50" y2="' + (5 + len) +
               '" stroke="#222" stroke-width="' + w +
               '" stroke-linecap="round" transform="rotate(' + angle + ' 50 50)"/>';
    }
    // Numerals
    var nums = "";
    for (var n = 1; n <= 12; n++) {
      var a = (n * 30) * Math.PI / 180;
      var nx = 50 + Math.sin(a) * 36;
      var ny = 50 - Math.cos(a) * 36 + 2.5;
      nums += '<text x="' + nx + '" y="' + ny + '" text-anchor="middle" ' +
              'font-family="Georgia, serif" font-size="7" font-weight="600" fill="#111">' + n + '</text>';
    }

    root.innerHTML =
      '<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:system-ui,sans-serif">' +
        '<svg id="face" viewBox="0 0 100 100" style="width:min(85vmin,85vh);height:min(85vmin,85vh);">' +
          '<defs>' +
            '<radialGradient id="bg" cx="50%" cy="40%" r="60%">' +
              '<stop offset="0%" stop-color="#fafafa"/>' +
              '<stop offset="100%" stop-color="#dcdcdc"/>' +
            '</radialGradient>' +
            '<radialGradient id="rim" cx="50%" cy="50%" r="50%">' +
              '<stop offset="92%" stop-color="#222"/>' +
              '<stop offset="100%" stop-color="#000"/>' +
            '</radialGradient>' +
          '</defs>' +
          '<circle cx="50" cy="50" r="49" fill="url(#rim)"/>' +
          '<circle cx="50" cy="50" r="46" fill="url(#bg)"/>' +
          ticks +
          nums +
          '<line id="h-hour"  x1="50" y1="55" x2="50" y2="28" stroke="#1a1a1a" stroke-width="3.5" stroke-linecap="round"/>' +
          '<line id="h-min"   x1="50" y1="56" x2="50" y2="14" stroke="#1a1a1a" stroke-width="2.2" stroke-linecap="round"/>' +
          '<line id="h-sec"   x1="50" y1="58" x2="50" y2="10" stroke="#d23" stroke-width="1.0" stroke-linecap="round"/>' +
          '<circle cx="50" cy="50" r="2.3" fill="#1a1a1a"/>' +
          '<circle cx="50" cy="50" r="1.0" fill="#d23"/>' +
        '</svg>' +
        '<div id="d" style="font-size:3.5vmin;opacity:0.65;font-weight:300;margin-top:2vmin;letter-spacing:0.05em"></div>' +
      '</div>';
    root._init = true;
  }

  var now = ctx.now;
  var s = now.getSeconds() + now.getMilliseconds() / 1000;
  var m = now.getMinutes() + s / 60;
  var h = (now.getHours() % 12) + m / 60;

  var hh = document.getElementById("h-hour");
  var hm = document.getElementById("h-min");
  var hs = document.getElementById("h-sec");
  if (hh) hh.setAttribute("transform", "rotate(" + (h * 30) + " 50 50)");
  if (hm) hm.setAttribute("transform", "rotate(" + (m * 6) + " 50 50)");
  if (hs) hs.setAttribute("transform", "rotate(" + (s * 6) + " 50 50)");

  var d = document.getElementById("d");
  if (d) d.textContent = ctx.showDate ? ctx.dateStr : "";
};
