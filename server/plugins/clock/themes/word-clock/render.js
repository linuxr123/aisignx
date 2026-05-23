/* Word Clock - tells the time in plain English. */
(function () {
  var HOURS = ["TWELVE","ONE","TWO","THREE","FOUR","FIVE","SIX","SEVEN",
               "EIGHT","NINE","TEN","ELEVEN"];

  function timeWords(now) {
    var h = now.getHours();
    var m = now.getMinutes();
    var rounded = Math.round(m / 5) * 5;
    if (rounded === 60) { rounded = 0; h = (h + 1) % 24; }
    var hourIdx = h % 12;
    var nextHour = (hourIdx + 1) % 12;

    if (rounded === 0)  return "IT IS " + HOURS[hourIdx] + " O'CLOCK";
    if (rounded === 5)  return "IT IS FIVE PAST " + HOURS[hourIdx];
    if (rounded === 10) return "IT IS TEN PAST " + HOURS[hourIdx];
    if (rounded === 15) return "IT IS QUARTER PAST " + HOURS[hourIdx];
    if (rounded === 20) return "IT IS TWENTY PAST " + HOURS[hourIdx];
    if (rounded === 25) return "IT IS TWENTY FIVE PAST " + HOURS[hourIdx];
    if (rounded === 30) return "IT IS HALF PAST " + HOURS[hourIdx];
    if (rounded === 35) return "IT IS TWENTY FIVE TO " + HOURS[nextHour];
    if (rounded === 40) return "IT IS TWENTY TO " + HOURS[nextHour];
    if (rounded === 45) return "IT IS QUARTER TO " + HOURS[nextHour];
    if (rounded === 50) return "IT IS TEN TO " + HOURS[nextHour];
    if (rounded === 55) return "IT IS FIVE TO " + HOURS[nextHour];
    return "";
  }

  window.renderClock = function (ctx) {
    var root = ctx.root;
    if (!root._init) {
      document.body.style.background = "radial-gradient(circle at 30% 20%,#1e1b3a 0%,#0a0612 100%)";
      document.body.style.color = "#fff";
      root.innerHTML =
        '<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:5vmin">' +
        '<div id="words" style="font-family:Georgia,serif;font-size:9vmin;font-weight:700;line-height:1.25;letter-spacing:0.04em;color:#fff;text-shadow:0 0 3vmin rgba(150,120,255,0.3)"></div>' +
        '<div id="exact" style="font-family:system-ui,sans-serif;font-size:3.5vmin;opacity:0.5;margin-top:4vmin;letter-spacing:0.15em"></div>' +
        '<div id="d" style="font-family:system-ui,sans-serif;font-size:3vmin;opacity:0.4;margin-top:1.5vmin;letter-spacing:0.1em"></div>' +
        '</div>';
      root._init = true;
    }
    var w = document.getElementById("words");
    var e = document.getElementById("exact");
    var d = document.getElementById("d");
    if (w) w.textContent = timeWords(ctx.now);
    if (e) e.textContent = ctx.timeStr;
    if (d) d.textContent = ctx.showDate ? ctx.dateStr : "";
  };
})();
