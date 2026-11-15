// Clock theme "word-clock": the classic letter grid that spells the time in English, to
// the nearest five minutes below; the dots under the grid count the minutes in between.
// English only (documented).
(function () {
  "use strict";
  var C = window.Clock;

  C.themes["word-clock"] = {
    mount: function (root, opts) {
      var s = C.stage(root, opts, "words");
      var grid = C.el("div", "clk-grid", s.face);
      grid.setAttribute("aria-hidden", "true");
      s.cells = {};
      C.GRID.forEach(function (row, r) {
        for (var c = 0; c < row.length; c++) {
          s.cells[r + "," + c] = C.el("span", "clk-letter", grid, row.charAt(c));
        }
      });
      var dots = C.el("div", "clk-dots", s.face);
      s.dots = [];
      for (var i = 0; i < 4; i++) s.dots.push(C.el("span", "clk-dot", dots));
      s.spoken = C.el("div", "clk-spoken", s.face);  // the phrase, for screen readers
      s.digital = C.el("div", "clk-digital", s.stage);  // the exact time, small
      s.h12 = opts.h12;
      s.key = "";
      return s;
    },
    tick: function (s, t) {
      var w = C.words(t.h, t.m, s.h12);
      var key = w.words.join(" ") + "|" + w.dots;
      if (key !== s.key) {
        s.key = key;
        var lit = C.litCells(w.words);
        Object.keys(s.cells).forEach(function (k) { s.cells[k].classList.toggle("on", !!lit[k]); });
        s.dots.forEach(function (d, i) { d.classList.toggle("on", i < w.dots); });
        C.setText(s.spoken, w.text);
      }
      C.setText(s.digital, t.time + (t.ampm ? " " + t.ampm : ""));
      C.setText(s.date, t.date);
    },
  };
})();
