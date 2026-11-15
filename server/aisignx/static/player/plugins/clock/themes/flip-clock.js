// Clock theme "flip-clock": split-flap style cards, one per digit, that flip when they change.
// Landscape shows the groups side by side; portrait stacks them (style.css).
(function () {
  "use strict";
  var C = window.Clock;

  function card(parent) {
    var c = C.el("div", "clk-card", parent);
    var digit = C.el("span", "clk-digit", c);
    C.el("span", "clk-hinge", c);
    return { node: c, digit: digit, value: null };
  }

  function group(parent, cls) {
    var g = C.el("div", "clk-group " + cls, parent);
    return { node: g, cards: [card(g), card(g)] };
  }

  // Shows `ch` on a card; a change restarts the flip animation (a no-op on the first draw).
  function show(c, ch) {
    if (c.value === ch) return;
    var first = c.value === null;
    c.value = ch;
    c.digit.textContent = ch;
    if (first) return;
    c.node.classList.remove("flip");
    void c.node.offsetWidth;  // restart the CSS animation
    c.node.classList.add("flip");
  }

  function fill(g, text) {
    show(g.cards[0], text.length > 1 ? text.charAt(0) : "");
    show(g.cards[1], text.charAt(text.length - 1));
    g.cards[0].node.classList.toggle("blank", g.cards[0].value === "");
  }

  C.themes["flip-clock"] = {
    mount: function (root, opts) {
      var s = C.stage(root, opts, "flip");
      var row = C.el("div", "clk-flip", s.face);
      s.hours = group(row, "hours");
      C.el("div", "clk-colon", row, ":");
      s.minutes = group(row, "minutes");
      s.seconds = null;
      if (opts.seconds) {
        C.el("div", "clk-colon", row, ":");
        s.seconds = group(row, "seconds");
      }
      s.ampm = C.el("div", "clk-ampm", s.face);
      if (!opts.h12) s.ampm.hidden = true;
      return s;
    },
    tick: function (s, t) {
      fill(s.hours, t.hh);
      fill(s.minutes, t.mm);
      if (s.seconds) fill(s.seconds, t.ss);
      C.setText(s.ampm, t.ampm);
      C.setText(s.date, t.date);
    },
  };
})();
