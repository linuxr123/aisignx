// AiSignX browser player: emergency overlay. Classic script, ES2018 at most.
// The manifest's "emergency" key is authoritative: an object shows the overlay, null hides it,
// an absent key (older server) leaves it as it is. Text via textContent only; colours via
// CSSOM so the strict CSP (style-src 'self') holds.
(function () {
  "use strict";

  var A = window.AiSignX = window.AiSignX || {};
  var LEVELS = { info: ["#1565c0", "#ffffff"], warning: ["#e65100", "#ffffff"], critical: ["#b71c1c", "#ffffff"] };
  var LAYOUTS = { fullscreen: 1, banner: 1, none: 1 };
  var COLOR = /^#[0-9a-fA-F]{3,8}$/;

  var box = document.getElementById("emergency");
  var titleEl = box && box.querySelector(".em-title");
  var msgEl = box && box.querySelector(".em-msg");
  var muted = null;  // {video, was}: the video this overlay muted, and its muted state before

  function color(v, fallback) { return typeof v === "string" && COLOR.test(v) ? v : fallback; }

  // The player sets current() to return the on-screen slide state ({slide, item}) or null.
  function currentVideo() {
    var cur = null;
    try { cur = E.current(); } catch (e) { cur = null; }
    var v = cur && cur.slide && cur.slide.media;
    return v && v.tagName === "VIDEO" ? v : null;
  }
  // Unmuting without a user gesture makes browsers without autoplay flags pause the video,
  // which would freeze the screen. Ask it to play again and fall back to muted playback.
  function restoreMute() {
    if (muted && !muted.was) {
      var v = muted.video;
      v.muted = false;
      if (!v.ended && typeof v.play === "function") {
        var p = v.play();
        if (p && typeof p.catch === "function") {
          p.catch(function () {
            v.muted = true;
            var again = v.play();
            if (again && typeof again.catch === "function") again.catch(function () {});
          });
        }
      }
    }
    muted = null;
  }
  // Fullscreen text silences the playing video; other layouts leave the sound alone.
  function sync() {
    var want = E.shownId !== null && E.layout === "fullscreen" ? currentVideo() : null;
    if (muted && muted.video !== want) restoreMute();
    if (want && !muted) { muted = { video: want, was: want.muted }; want.muted = true; }
  }

  function show(e) {
    if (!box || !e || typeof e !== "object" || e.id == null) return;
    var level = LEVELS[e.level] ? e.level : "critical";
    var layout = LAYOUTS[e.layout] ? e.layout : "fullscreen";  // unknown layout: fail safe to text
    E.shownId = e.id;
    E.layout = layout;
    titleEl.textContent = typeof e.title === "string" ? e.title : "";
    msgEl.textContent = typeof e.message === "string" ? e.message : "";
    msgEl.hidden = !msgEl.textContent;
    box.className = "layout-" + layout + " level-" + level;
    box.style.backgroundColor = color(e.background, LEVELS[level][0]);
    box.style.color = color(e.foreground, LEVELS[level][1]);
    box.hidden = layout === "none";
    sync();
  }
  function hide() {
    E.shownId = null;
    E.layout = null;
    if (box) {
      box.hidden = true;
      box.className = "";
      titleEl.textContent = "";
      msgEl.textContent = "";
    }
    sync();
  }
  function apply(m) {
    if (!m || typeof m !== "object" || !Object.prototype.hasOwnProperty.call(m, "emergency")) return;
    if (m.emergency && typeof m.emergency === "object") show(m.emergency);
    else hide();
  }
  function clearIf(id) {
    if (id != null && E.shownId !== null && String(id) === String(E.shownId)) hide();
  }

  var E = A.emergency = {
    shownId: null,
    layout: null,
    apply: apply,
    show: show,
    hide: hide,
    clearIf: clearIf,
    sync: sync,
    current: function () { return null; },
  };
})();
