(function () {
  const cfg = window.PLUGIN_CONFIG || {};
  const meta = window.PLUGIN_META || {};
  const root = document.getElementById('root');

  const themeName = (cfg.theme || 'minimal-dark');
  const format = (cfg.format || 'HH:MM:SS');
  const showDate = cfg.date !== false;

  // Reset root layout - themes get a clean canvas
  root.innerHTML = '';
  root.style.cssText = 'width:100vw;height:100vh;position:relative;overflow:hidden;';
  document.body.style.background = '#000';
  document.body.style.color = '#fff';

  // Resolve "now" against the server clock when the parent player has
  // calibrated one (plugin_runner.html exposes window.signageDate). Falls
  // back to the OS clock when running standalone or before the first
  // /server_time round-trip completes. This is what keeps a fleet of
  // mixed hardware showing the SAME time on every screen, even when one
  // box's system clock has drifted away from NTP.
  function getNow() {
    try {
      if (typeof window.signageDate === 'function') return window.signageDate();
    } catch (_) {}
    return new Date();
  }

  function getTimeParts(now) {
    now = now || getNow();
    const h24 = now.getHours();
    const h12 = h24 % 12 || 12;
    const mm  = String(now.getMinutes()).padStart(2, '0');
    const ss  = String(now.getSeconds()).padStart(2, '0');
    const ampm = h24 >= 12 ? 'PM' : 'AM';
    const hh24 = String(h24).padStart(2, '0');
    const hh12 = String(h12).padStart(2, '0');
    let timeStr;
    switch (format) {
      case 'HH:MM':    timeStr = hh24 + ':' + mm; break;
      case 'hh:MM:SS': timeStr = hh12 + ':' + mm + ':' + ss + ' ' + ampm; break;
      case 'hh:MM':    timeStr = hh12 + ':' + mm + ' ' + ampm; break;
      default:         timeStr = hh24 + ':' + mm + ':' + ss;
    }
    const dateStr = now.toLocaleDateString(undefined, {
      weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
    });
    return { now: now, h24: h24, h12: h12, mm: mm, ss: ss, ampm: ampm,
             hh24: hh24, hh12: hh12, timeStr: timeStr, dateStr: dateStr };
  }

  function themeAsset(file) {
    const base = '/plugin_assets/' + (meta.type || 'clock') + '/themes/' + themeName + '/';
    return base + file;
  }

  function buildCtx() {
    return Object.assign({}, getTimeParts(), {
      cfg: cfg,
      showDate: showDate,
      themeName: themeName,
      themeAsset: themeAsset,
      root: root
    });
  }

  function loadAndStart() {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = themeAsset('style.css');
    link.onerror = function () { link.remove(); };
    document.head.appendChild(link);

    const script = document.createElement('script');
    script.src = themeAsset('render.js');
    script.onload = function () {
      if (typeof window.renderClock !== 'function') {
        renderFallback('Theme "' + themeName + '" did not define renderClock()');
        return;
      }
      const tick = function () {
        try { window.renderClock(buildCtx()); }
        catch (e) { console.error('[clock] theme render error:', e); }
      };
      tick();
      setInterval(tick, 1000);
    };
    script.onerror = function () { renderFallback('Could not load theme: ' + themeName); };
    document.head.appendChild(script);
  }

  function renderFallback(msg) {
    root.innerHTML =
      '<div style="position:absolute;inset:0;display:flex;flex-direction:column;' +
      'align-items:center;justify-content:center;background:#000;color:#fff;' +
      'font-family:system-ui,sans-serif;text-align:center;padding:2vmin">' +
      '<div id="fallback-time" style="font-size:14vmin;font-weight:700;letter-spacing:-0.02em"></div>' +
      '<div id="fallback-date" style="font-size:3.5vmin;opacity:0.7;margin-top:1vmin"></div>' +
      '<div style="position:absolute;bottom:1vmin;font-size:1.5vmin;opacity:0.4">' + msg + '</div>' +
      '</div>';
    const tEl = document.getElementById('fallback-time');
    const dEl = document.getElementById('fallback-date');
    const tick = function () {
      const ctx = buildCtx();
      tEl.textContent = ctx.timeStr;
      dEl.textContent = showDate ? ctx.dateStr : '';
    };
    tick();
    setInterval(tick, 1000);
  }

  loadAndStart();

  if (!cfg.loop) {
    const dur = Math.max(5, parseInt(cfg.duration || 30, 10)) * 1000;
    setTimeout(function () {
      try { window.parent.postMessage({ type: 'signage:complete' }, '*'); } catch (e) {}
    }, dur);
  }
})();
