// Clock themes "minimal-dark" and "minimal-light": large digits, small seconds, the date
// below. One file because the two differ only in colour (style.css, body.theme-*).
(function () {
  "use strict";
  var C = window.Clock;

  var minimal = {
    mount: function (root, opts) {
      var s = C.stage(root, opts, "minimal");
      var time = C.el("div", "clk-time", s.face);
      s.main = C.el("span", "clk-hm", time);
      // AM/PM above the seconds, to the right of the digits.
      var side = C.el("span", "clk-side", time);
      s.ampm = C.el("span", "clk-ampm", side);
      s.sec = C.el("span", "clk-sec", side);
      if (!opts.seconds) s.sec.hidden = true;
      if (!opts.h12) s.ampm.hidden = true;
      if (!opts.seconds && !opts.h12) side.hidden = true;
      return s;
    },
    tick: function (s, t) {
      C.setText(s.main, t.hh + ":" + t.mm);
      C.setText(s.sec, t.ss);
      C.setText(s.ampm, t.ampm);
      C.setText(s.date, t.date);
    },
  };

  C.themes["minimal-dark"] = minimal;
  C.themes["minimal-light"] = minimal;
})();
