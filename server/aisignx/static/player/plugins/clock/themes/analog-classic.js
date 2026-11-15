// Clock theme "analog-classic": an SVG dial built with createElementNS. Beside it
// (landscape) or below it (portrait): the label, the digital time and the date.
(function () {
  "use strict";
  var C = window.Clock;
  var NS = "http://www.w3.org/2000/svg";

  function svg(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs).forEach(function (k) { e.setAttribute(k, String(attrs[k])); });
    if (parent) parent.appendChild(e);
    return e;
  }

  // A hand pointing up from the centre (100,100); rotated by tick().
  function hand(parent, cls, length, tail, width) {
    var g = svg("g", { "class": cls }, parent);
    svg("line", { x1: 100, y1: 100 + tail, x2: 100, y2: 100 - length, "stroke-width": width,
                  "stroke-linecap": "round" }, g);
    return g;
  }

  function dial(parent, opts) {
    var root = svg("svg", { viewBox: "0 0 200 200", "class": "clk-dial", role: "img",
                            "aria-label": "Clock" }, parent);
    svg("circle", { cx: 100, cy: 100, r: 97, "class": "clk-rim" }, root);
    svg("circle", { cx: 100, cy: 100, r: 93, "class": "clk-plate" }, root);
    for (var i = 0; i < 60; i++) {
      var major = i % 5 === 0;
      var tick = svg("line", { x1: 100, y1: major ? 11 : 12, x2: 100, y2: major ? 23 : 17,
                               "class": major ? "clk-mark major" : "clk-mark",
                               transform: "rotate(" + i * 6 + " 100 100)" }, root);
      tick.setAttribute("stroke-linecap", "round");
    }
    for (var n = 1; n <= 12; n++) {
      var a = n * Math.PI / 6;
      var num = svg("text", { x: (100 + 62 * Math.sin(a)).toFixed(2),
                              y: (100 - 62 * Math.cos(a)).toFixed(2),
                              "class": "clk-num", "text-anchor": "middle",
                              "dominant-baseline": "central" }, root);
      num.textContent = String(n);
    }
    var hands = {
      hour: hand(root, "clk-hand hour", 46, 10, 6.5),
      minute: hand(root, "clk-hand minute", 70, 12, 4.5),
      second: null,
    };
    if (opts.seconds) {
      hands.second = hand(root, "clk-hand second", 76, 20, 1.6);
      svg("circle", { cx: 100, cy: 100, r: 4.5, "class": "clk-cap second" }, root);
    }
    svg("circle", { cx: 100, cy: 100, r: opts.seconds ? 2 : 5, "class": "clk-cap" }, root);
    return hands;
  }

  function turn(g, deg) {
    if (g) g.setAttribute("transform", "rotate(" + deg.toFixed(2) + " 100 100)");
  }

  C.themes["analog-classic"] = {
    mount: function (root, opts) {
      var s = C.stage(root, opts, "analog");
      s.hands = dial(s.face, opts);
      s.digital = C.el("div", "clk-digital", s.stage);
      return s;
    },
    tick: function (s, t) {
      var a = C.angles(t);
      turn(s.hands.hour, a.hour);
      turn(s.hands.minute, a.minute);
      turn(s.hands.second, a.second);
      C.setText(s.digital, t.time + (t.ampm ? " " + t.ampm : ""));
      C.setText(s.date, t.date);
    },
  };
})();
