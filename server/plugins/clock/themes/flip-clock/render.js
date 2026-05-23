/* Flip Clock - retro split-flap card style. No actual flip animation
   (intentional, simple/readable), but the styling evokes mechanical flip clocks. */
window.renderClock = function (ctx) {
  var root = ctx.root;
  if (!root._init) {
    document.body.style.background = "linear-gradient(180deg,#1a1a22 0%,#0a0a12 100%)";
    document.body.style.color = "#fff";

    var style = document.createElement("style");
    style.textContent =
      ".flip-card{display:inline-block;background:linear-gradient(180deg,#222 0%,#111 49%,#000 51%,#222 100%);" +
      "color:#fff;border-radius:1.5vmin;padding:0;width:18vmin;height:24vmin;line-height:24vmin;text-align:center;" +
      "font-family:'Courier New',monospace;font-size:20vmin;font-weight:700;" +
      "box-shadow:inset 0 0 1vmin rgba(0,0,0,0.7),0 0.5vmin 2vmin rgba(0,0,0,0.6);" +
      "position:relative;overflow:hidden}" +
      ".flip-card::after{content:'';position:absolute;left:0;right:0;top:50%;height:0.2vmin;" +
      "background:rgba(0,0,0,0.6);box-shadow:0 0 0.5vmin rgba(0,0,0,0.6)}" +
      ".flip-sep{display:inline-block;font-size:14vmin;color:#fff;font-weight:700;width:4vmin;text-align:center;" +
      "font-family:'Courier New',monospace}" +
      ".flip-row{display:flex;align-items:center;justify-content:center;gap:1.5vmin}" +
      ".flip-ampm{display:inline-block;font-size:5vmin;color:#bbb;letter-spacing:0.1em;margin-left:1.5vmin;" +
      "font-family:system-ui,sans-serif;font-weight:600}" +
      ".flip-date{margin-top:3vmin;font-size:3.5vmin;letter-spacing:0.08em;color:#aaa;font-family:system-ui,sans-serif}";
    document.head.appendChild(style);

    root.innerHTML =
      '<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center">' +
      '<div class="flip-row">' +
      '<div class="flip-card" id="hh1">0</div>' +
      '<div class="flip-card" id="hh2">0</div>' +
      '<div class="flip-sep">:</div>' +
      '<div class="flip-card" id="mm1">0</div>' +
      '<div class="flip-card" id="mm2">0</div>' +
      '<div class="flip-sep" id="sep2" style="display:none">:</div>' +
      '<div class="flip-card" id="ss1" style="display:none">0</div>' +
      '<div class="flip-card" id="ss2" style="display:none">0</div>' +
      '<div class="flip-ampm" id="ampm" style="display:none"></div>' +
      '</div>' +
      '<div class="flip-date" id="d"></div>' +
      '</div>';
    root._init = true;
  }

  var fmt = (ctx.cfg && ctx.cfg.format) || "HH:MM:SS";
  var is12 = fmt.indexOf("hh") === 0;
  var hasSec = fmt.indexOf(":SS") >= 0;

  var hh = is12 ? ctx.hh12 : ctx.hh24;
  var mm = ctx.mm;
  var ss = ctx.ss;

  document.getElementById("hh1").textContent = hh.charAt(0);
  document.getElementById("hh2").textContent = hh.charAt(1);
  document.getElementById("mm1").textContent = mm.charAt(0);
  document.getElementById("mm2").textContent = mm.charAt(1);

  var s1 = document.getElementById("ss1");
  var s2 = document.getElementById("ss2");
  var sep = document.getElementById("sep2");
  if (hasSec) {
    s1.style.display = ""; s2.style.display = ""; sep.style.display = "";
    s1.textContent = ss.charAt(0);
    s2.textContent = ss.charAt(1);
  } else {
    s1.style.display = "none"; s2.style.display = "none"; sep.style.display = "none";
  }

  var ap = document.getElementById("ampm");
  if (is12) { ap.style.display = ""; ap.textContent = ctx.ampm; }
  else      { ap.style.display = "none"; }

  var d = document.getElementById("d");
  if (d) d.textContent = ctx.showDate ? ctx.dateStr : "";
};
