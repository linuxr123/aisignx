// AiSignX weather plugin: WMO code texts and SVG icons, built with createElementNS only.
// Colours come from classes (style.css), so the light and dark themes can restyle them.
// Exposes window.WxIcons = {icon(code, isDay), stat(name), text(code), mood(code, isDay)}.
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";

  var TEXT = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Sleet", 67: "Heavy sleet",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, hail",
  };

  // code -> [sky, precipitation, amount]
  //   sky: clear | few | part | cloud | dark | fog ; precipitation: none | drizzle | rain |
  //   sleet | snow | thunder ; amount 1..3
  function kind(code) {
    var c = Number(code);
    if (c === 0) return ["clear", "none", 0];
    if (c === 1) return ["few", "none", 0];
    if (c === 2) return ["part", "none", 0];
    if (c === 3) return ["cloud", "none", 0];
    if (c === 45 || c === 48) return ["fog", "none", 0];
    if (c >= 51 && c <= 55) return ["cloud", "drizzle", (c - 49) / 2];
    if (c === 56 || c === 57) return ["cloud", "sleet", 1];
    if (c >= 61 && c <= 65) return [c === 65 ? "dark" : "cloud", "rain", (c - 59) / 2];
    if (c === 66 || c === 67) return ["cloud", "sleet", c - 65];
    if (c >= 71 && c <= 77) return ["cloud", "snow", c === 77 ? 1 : (c - 69) / 2];
    if (c >= 80 && c <= 82) return ["part", "rain", c - 79];
    if (c === 85 || c === 86) return ["part", "snow", c - 84];
    if (c >= 95) return ["dark", "thunder", 2];
    return null;
  }

  function node(tag, attrs, cls) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, String(attrs[k])); });
    if (cls) e.setAttribute("class", cls);
    return e;
  }
  function svg(cls) {
    var s = node("svg", { viewBox: "0 0 64 64", "aria-hidden": "true", focusable: "false" }, cls);
    return s;
  }
  function group(parent, transform) {
    return parent.appendChild(node("g", transform ? { transform: transform } : {}));
  }

  function sun(p, cx, cy, r) {
    var g = group(p);
    var rays = node("g", {}, "wx-i-ray");
    for (var i = 0; i < 8; i++) {
      var a = i * Math.PI / 4, r1 = r * 1.45, r2 = r * 1.95;
      rays.appendChild(node("line", {
        x1: (cx + Math.cos(a) * r1).toFixed(2), y1: (cy + Math.sin(a) * r1).toFixed(2),
        x2: (cx + Math.cos(a) * r2).toFixed(2), y2: (cy + Math.sin(a) * r2).toFixed(2),
      }));
    }
    g.appendChild(rays);
    g.appendChild(node("circle", { cx: cx, cy: cy, r: r }, "wx-i-sun"));
  }
  function moon(p, cx, cy, r) {
    // A crescent: the outer circle minus an offset circle, drawn as two arcs.
    var d = "M" + (cx + r * 0.25) + " " + (cy - r) +
      " A" + r + " " + r + " 0 1 0 " + (cx + r) + " " + (cy + r * 0.35) +
      " A" + (r * 0.8) + " " + (r * 0.8) + " 0 1 1 " + (cx + r * 0.25) + " " + (cy - r) + " Z";
    group(p).appendChild(node("path", { d: d }, "wx-i-moon"));
  }
  // A cloud whose flat bottom runs from x to x + w at height y.
  function cloud(p, x, y, w, cls) {
    var h = w * 0.62, s = w / 40;
    var d = "M" + (x + 7 * s) + " " + y +
      " H" + (x + 33 * s) +
      " A" + (7 * s) + " " + (7 * s) + " 0 0 0 " + (x + 34.5 * s) + " " + (y - 13.9 * s) +
      " A" + (11 * s) + " " + (11 * s) + " 0 0 0 " + (x + 13.5 * s) + " " + (y - 15.5 * s) +
      " A" + (8 * s) + " " + (8 * s) + " 0 0 0 " + (x + 7 * s) + " " + y + " Z";
    group(p).appendChild(node("path", { d: d }, cls || "wx-i-cloud"));
    return h;
  }
  function drops(p, n, len, cls) {
    var g = node("g", {}, cls);
    var xs = n === 1 ? [30] : n === 2 ? [24, 36] : [20, 30, 40];
    xs.forEach(function (x, i) {
      var y = 48 + (i % 2) * 3;
      g.appendChild(node("line", { x1: x + 2, y1: y, x2: x - 1, y2: y + len }));
    });
    p.appendChild(g);
  }
  function flakes(p, n) {
    var g = node("g", {}, "wx-i-flake");
    var xs = n === 1 ? [30] : n === 2 ? [23, 37] : [19, 30, 41];
    xs.forEach(function (x, i) {
      var y = 52 + (i % 2) * 4, r = 3.6;
      for (var k = 0; k < 3; k++) {
        var a = k * Math.PI / 3;
        g.appendChild(node("line", {
          x1: (x - Math.cos(a) * r).toFixed(2), y1: (y - Math.sin(a) * r).toFixed(2),
          x2: (x + Math.cos(a) * r).toFixed(2), y2: (y + Math.sin(a) * r).toFixed(2),
        }));
      }
    });
    p.appendChild(g);
  }
  function bolt(p) {
    p.appendChild(node("path", { d: "M33 42 L25 54 H31 L27 63 L39 49 H33 L37 42 Z" }, "wx-i-bolt"));
  }
  function fog(p) {
    var g = node("g", {}, "wx-i-fog");
    [[14, 50, 50], [10, 42, 56], [18, 58, 46]].forEach(function (l) {
      g.appendChild(node("line", { x1: l[0], y1: l[1] - 8, x2: l[2], y2: l[1] - 8 }));
    });
    p.appendChild(g);
  }
  function celestial(p, isDay, cx, cy, r) {
    if (isDay === false) moon(p, cx, cy, r); else sun(p, cx, cy, r);
  }

  // icon(code, isDay): an <svg> for a WMO code ("?" style circle when unknown).
  function icon(code, isDay) {
    var s = svg("wx-icon");
    var k = kind(code);
    if (!k) {
      s.appendChild(node("circle", { cx: 32, cy: 32, r: 12 }, "wx-i-cloud"));
      return s;
    }
    var sky = k[0], fall = k[1], amount = k[2];
    var wet = fall !== "none";
    if (sky === "clear") { celestial(s, isDay, 32, 32, isDay === false ? 15 : 12); return s; }
    if (sky === "fog") { cloud(s, 12, 34, 40, "wx-i-cloud"); fog(s); return s; }
    if (sky === "few" || sky === "part") {
      celestial(s, isDay, sky === "few" ? 26 : 24, wet ? 20 : 24, sky === "few" ? 10 : 9);
      cloud(s, sky === "few" ? 26 : 16, wet ? 42 : 50, sky === "few" ? 30 : 40, "wx-i-cloud");
    } else if (sky === "dark") {
      cloud(s, 22, 32, 32, "wx-i-cloud2");
      cloud(s, 8, 42, 42, "wx-i-cloud-dark");
    } else {
      if (!wet) cloud(s, 24, 34, 32, "wx-i-cloud2");
      cloud(s, wet ? 11 : 8, wet ? 42 : 50, wet ? 42 : 44, "wx-i-cloud");
    }
    if (fall === "drizzle") drops(s, Math.max(1, Math.round(amount)), 5, "wx-i-drop");
    if (fall === "rain") drops(s, Math.min(3, Math.round(amount) + 1), amount > 2 ? 11 : 8, "wx-i-drop");
    if (fall === "sleet") { drops(s, 1, 7, "wx-i-drop"); flakes(s, 2); }
    if (fall === "snow") flakes(s, Math.min(3, Math.round(amount) + 1));
    if (fall === "thunder") { bolt(s); drops(s, 2, 6, "wx-i-drop wx-i-drop-side"); }
    return s;
  }

  // Small line icons for the detail tiles (stroke: currentColor).
  function stat(name) {
    var s = svg("wx-stat-icon");
    var g = s.appendChild(node("g", {}, "wx-i-line"));
    function path(d) { g.appendChild(node("path", { d: d })); }
    if (name === "wind") {
      path("M8 24 H40 A7 7 0 1 0 33 17");
      path("M8 34 H50 A7 7 0 1 1 43 41");
      path("M8 44 H30");
    } else if (name === "humidity") {
      path("M32 8 C24 20 16 29 16 39 A16 16 0 0 0 48 39 C48 29 40 20 32 8 Z");
    } else if (name === "rain") {
      path("M8 32 A24 24 0 0 1 56 32 Z");
      path("M32 32 V50 A5 5 0 0 1 22 50");
    } else if (name === "sunrise" || name === "sunset") {
      path("M18 44 A14 14 0 0 1 46 44");
      path("M8 52 H56");
      path(name === "sunrise" ? "M32 8 V24 M25 15 L32 8 L39 15" : "M32 8 V24 M25 17 L32 24 L39 17");
    } else if (name === "feels") {
      path("M27 10 A5 5 0 0 1 37 10 V38 A10 10 0 1 1 27 38 Z");
      path("M32 26 V44");
    }
    return s;
  }

  // An arrow pointing where the wind blows to (from `deg`, meteorological).
  function arrow(deg) {
    var s = svg("wx-arrow");
    var g = s.appendChild(node("g", { transform: "rotate(" + ((Number(deg) || 0) + 180) + " 32 32)" }, "wx-i-arrow"));
    g.appendChild(node("path", { d: "M32 6 L46 46 L32 38 L18 46 Z" }));
    return s;
  }

  // mood(code, isDay): the background family for the "auto" theme.
  function mood(code, isDay) {
    var k = kind(code);
    var night = isDay === false;
    if (!k) return night ? "clear-night" : "cloud-day";
    if (k[1] === "thunder") return "storm";
    if (k[1] === "snow" || k[1] === "sleet") return night ? "snow-night" : "snow";
    if (k[1] !== "none") return night ? "rain-night" : "rain";
    if (k[0] === "fog") return "fog";
    if (k[0] === "cloud" || k[0] === "dark") return night ? "cloud-night" : "cloud-day";
    return night ? "clear-night" : "clear-day";
  }

  window.WxIcons = {
    icon: icon,
    stat: stat,
    arrow: arrow,
    mood: mood,
    kind: kind,
    text: function (code) { return TEXT[code] || ""; },
  };
})();
