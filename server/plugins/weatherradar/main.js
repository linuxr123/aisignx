(function () {
  "use strict";

  console.log('[WeatherRadar] plugin loaded - build 2026-04-25j nowcast');

  // -- Configuration -----------------------------------------------------------
  var cfgRaw = window.PLUGIN_CONFIG || {};
  var cfg = {
    location:        String(cfgRaw.location        || "").trim(),
    zoom:            Math.min(12, Math.max(4, parseInt(cfgRaw.zoom           != null ? cfgRaw.zoom        : 8,   10))),
    frames:          Math.min(60, Math.max(1, parseInt(cfgRaw.frames         != null ? cfgRaw.frames      : 10,  10))),
    frame_ms:        Math.max(100,             parseInt(cfgRaw.frame_ms      != null ? cfgRaw.frame_ms    : 600, 10)),
    alerts:          cfgRaw.alerts !== false,
    nowcast:         cfgRaw.nowcast === true || cfgRaw.nowcast === "true",
    refresh_minutes: Math.max(0, parseFloat(cfgRaw.refresh_minutes           != null ? cfgRaw.refresh_minutes : 5))
  };

  // -- Root / body setup -------------------------------------------------------
  var root = document.getElementById("root");
  if (!root) { root = document.createElement("div"); root.id = "root"; document.body.appendChild(root); }

  function css(el, styles) { Object.keys(styles).forEach(function(k){ el.style[k] = styles[k]; }); }

  css(document.body, { width:"100%", height:"100%", margin:"0", padding:"0", overflow:"hidden", background:"#0d1117" });
  css(root, { width:"100%", height:"100%", margin:"0", padding:"0", overflow:"hidden", background:"#0d1117", position:"relative", display:"block" });

  // -- Status message ----------------------------------------------------------
  function showMessage(msg, isError) {
    root.innerHTML = "";
    var d = document.createElement("div");
    css(d, {
      position:"absolute", top:"0", left:"0", right:"0", bottom:"0",
      display:"flex", alignItems:"center", justifyContent:"center",
      color: isError ? "#ff6b6b" : "#aaa", fontSize:"2.8vmin",
      fontFamily:"sans-serif", textAlign:"center", padding:"4vmin"
    });
    d.textContent = msg;
    root.appendChild(d);
  }

  // -- Location parsing --------------------------------------------------------
  function parseLocation(loc) {
    if (!loc) return null;
    var s = loc.trim();
    var m = s.match(/^\s*(-?\d+(\.\d+)?)\s*,\s*(-?\d+(\.\d+)?)\s*$/);
    if (m) return { lat: parseFloat(m[1]), lon: parseFloat(m[3]), label: s };
    if (/^\d{5}(-\d{4})?$/.test(s)) return { zip: s.slice(0, 5) };
    return { query: s };
  }

  async function geocodeZip(zip) {
    // Primary source: zippopotam.us (purpose-built ZIP→lat/lon JSON, no key).
    try {
      var _fetch = window.cachedFetch || fetch;
      var r = await _fetch("https://api.zippopotam.us/us/" + zip, { signal: AbortSignal.timeout(10000) });
      if (r.ok) {
        var d = await r.json();
        if (d.places && d.places.length) {
          var p = d.places[0];
          return { lat: parseFloat(p.latitude), lon: parseFloat(p.longitude),
                   label: p["place name"] + ", " + p["state abbreviation"] + " " + zip };
        }
      } else {
        console.warn("[WeatherRadar] zippopotam HTTP " + r.status);
      }
    } catch(e) { console.warn("[WeatherRadar] zippopotam geocode failed:", e); }

    // Fallback: Nominatim (OpenStreetMap). Free, no key, returns a result
    // for virtually any US ZIP zippopotam couldn't serve (network blocked,
    // outage, recently-issued ZIP, etc.). Without this fallback the radar
    // dies on "Unknown zip code: NNNNN" any time zippopotam is unreachable.
    try {
      var _fetch2 = window.cachedFetch || fetch;
      var u = "https://nominatim.openstreetmap.org/search?postalcode="
            + encodeURIComponent(zip) + "&country=us&format=json&limit=1";
      var r2 = await _fetch2(u, { signal: AbortSignal.timeout(10000) });
      if (r2.ok) {
        var arr = await r2.json();
        if (Array.isArray(arr) && arr.length) {
          var hit = arr[0];
          return { lat: parseFloat(hit.lat), lon: parseFloat(hit.lon),
                   label: (hit.display_name || ("ZIP " + zip)).split(",").slice(0, 2).join(",").trim() + " " + zip };
        }
      }
    } catch(e) { console.warn("[WeatherRadar] nominatim zip fallback failed:", e); }
    return null;
  }

  async function geocodeCity(query) {
    try {
      var _fetch = window.cachedFetch || fetch;
      var r = await _fetch("https://geocoding-api.open-meteo.com/v1/search?name=" + encodeURIComponent(query) + "&count=1&format=json", { signal: AbortSignal.timeout(10000) });
      if (!r.ok) throw new Error("HTTP " + r.status);
      var d = await r.json();
      if (d.results && d.results.length) {
        var g = d.results[0];
        var lbl = g.admin1 ? g.name + ", " + g.admin1 : g.name + ", " + (g.country || "");
        return { lat: g.latitude, lon: g.longitude, label: lbl };
      }
    } catch(e) { console.warn("[WeatherRadar] city geocode failed:", e); }
    return null;
  }

  // -- RainViewer API ----------------------------------------------------------
  // Free public API — no key required.
  // Returns a list of available radar frame Unix timestamps.
  var RAINVIEWER_API = "https://api.rainviewer.com/public/weather-maps.json";

  // Tile URL template — {ts} = Unix timestamp, standard slippy-map z/x/y
  // color=2 (original), smooth=1, snow=1
  var RV_TILE_TPL = "https://tilecache.rainviewer.com{path}/256/{z}/{x}/{y}/2/1_1.png";

  // NWS storm-alerts WMS (polygon overlays)
  var ALERTS_WMS = "https://opengeo.ncep.noaa.gov/geoserver/conus/conus_hazards/ows";

  async function fetchRainViewerFrames() {
    try {
      // cachedFetch: when offline we serve the last-known frame list. Tiles
      // for those timestamps may already be in the browser HTTP cache;
      // the radar will play whatever frames are available even if the
      // most recent ones aren't.
      var _fetch = window.cachedFetch || fetch;
      var r = await _fetch(RAINVIEWER_API, { signal: AbortSignal.timeout(15000) });
      if (!r.ok) throw new Error("HTTP " + r.status);
      var d = await r.json();
      // d.radar.past    = past frames (~13 entries, last ~2h, oldest first)
      // d.radar.nowcast = forecast frames (~3 entries, +10/+20/+30 min)
      var past    = (d.radar && d.radar.past)    ? d.radar.past    : [];
      var nowcast = (d.radar && d.radar.nowcast) ? d.radar.nowcast : [];
      var combined = cfg.nowcast ? past.concat(nowcast) : past;
      // Take the most-recent cfg.frames entries (cap at what's actually available).
      var sliced = combined.slice(-cfg.frames);
      console.log('[WeatherRadar] RainViewer: past=' + past.length +
                  ' nowcast=' + nowcast.length +
                  ' requested=' + cfg.frames +
                  ' returning=' + sliced.length);
      return sliced;
    } catch(e) {
      console.error("[WeatherRadar] RainViewer API failed:", e);
    }
    return [];
  }

  function fmtUnix(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    var h = String(d.getHours()).padStart(2,"0");
    var m = String(d.getMinutes()).padStart(2,"0");
    // Short local timezone abbreviation (e.g. "EDT", "PST"). Falls back
    // gracefully on browsers/runtimes that don't support timeZoneName:'short'.
    var tz = "";
    try {
      var parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
        .formatToParts(d);
      var tzPart = parts.find(function(p){ return p.type === "timeZoneName"; });
      if (tzPart) tz = " " + tzPart.value;
    } catch (_) {}
    return h + ":" + m + tz;
  }

  // -- Load Leaflet ------------------------------------------------------------
  function loadLeaflet() {
    return new Promise(function(resolve) {
      if (window.L) { resolve(); return; }
      var lnk = document.createElement("link");
      lnk.rel  = "stylesheet";
      lnk.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
      document.head.appendChild(lnk);
      var scr = document.createElement("script");
      scr.src    = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
      scr.onload = resolve;
      document.head.appendChild(scr);
    });
  }

  // -- Build map ---------------------------------------------------------------
  function buildMap(lat, lon, label, frames) {
    root.innerHTML = "";

    // Map container
    var mapDiv = document.createElement("div");
    mapDiv.id = "radar-map";
    css(mapDiv, { position:"absolute", top:"0", left:"0", right:"0", bottom:"0" });
    root.appendChild(mapDiv);

    var map = L.map("radar-map", {
      center:             [lat, lon],
      zoom:               cfg.zoom,
      minZoom:            2,
      maxZoom:            12,
      zoomControl:        false,
      attributionControl: false,
      preferCanvas:       true
    });

    // Base layer — OpenStreetMap tiles, made dark via CSS filter on the
    // map container. OSM is more reliable than CartoDB and never returns
    // "zoom level not supported" warnings within range.
    mapDiv.style.background = "#1a1a1a";
    mapDiv.style.filter = "invert(0.92) hue-rotate(180deg) saturate(0.85) brightness(0.95)";
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom:       12,
      maxNativeZoom: 12,
      minZoom:        2,
      crossOrigin:   true,
      errorTileUrl:  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    }).addTo(map);

    // RainViewer tile coverage at the 256px endpoint caps at z7 -- above
    // that the server returns "Zoom Level Not Supported" placeholder PNGs.
    // maxNativeZoom:7 makes Leaflet request z7 tiles and scale them up
    // for higher map zooms instead of fetching the placeholder.
    var radarLayers = frames.map(function(f) {
      var url = RV_TILE_TPL.replace("{path}", f.path);
      var layer = L.tileLayer(url, {
        opacity:       0.85,
        maxZoom:       12,
        maxNativeZoom: 7,
        tileSize:      256,
        zIndex:        500,
        crossOrigin:   true,
        errorTileUrl:  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
      });
      layer.on("tileload", function(e) {
        // Counter-rotate the dark filter for radar tiles
        e.tile.style.filter = "invert(1) hue-rotate(180deg) saturate(1.18) brightness(1.05)";
      });
      return layer;
    });

    // NWS alerts WMS overlay
    if (cfg.alerts) {
      L.tileLayer.wms(ALERTS_WMS, {
        layers:      "conus_hazards",
        format:      "image/png",
        transparent: true,
        opacity:     0.55,
        version:     "1.3.0",
        zIndex:      600
      }).addTo(map);
    }

    L.control.zoom({ position: "topright" }).addTo(map);

    // -- Animation state -------------------------------------------------------
    var currentFrame = 0;
    var animTimer    = null;
    var playing      = false;

    // Keep ALL radar layers on the map at all times. Toggling visibility via
    // setOpacity(0/0.85) instead of add/removeLayer keeps the tiles in the
    // DOM so they don't get re-fetched on every animation cycle. This makes
    // the loop both smoother and much lighter on the network.
    var FRAME_OPACITY = 0.85;
    radarLayers.forEach(function(lyr, i) {
      map.addLayer(lyr);
      lyr.setOpacity(0);
    });

    function showFrame(idx) {
      radarLayers.forEach(function(lyr, i) {
        lyr.setOpacity(i === idx ? FRAME_OPACITY : 0);
      });
      currentFrame = idx;
      updateUI();
    }

    function startPlay() {
      if (playing) return;
      playing = true;
      if (playBtn) playBtn.textContent = "⏸";
      animTimer = setInterval(function() {
        showFrame((currentFrame + 1) % radarLayers.length);
      }, cfg.frame_ms);
    }

    function stopPlay() {
      playing = false;
      if (playBtn) playBtn.textContent = "▶";
      clearInterval(animTimer);
      animTimer = null;
    }

    // Show the most-recent frame straight away while tiles for the other
    // frames load in the background. Start the animation after a 1.2s
    // preload so the first cycle has every frame ready to display.
    // The HUD / scrubber / playBtn must be built FIRST because showFrame()
    // calls updateUI() which references timeEl and ticks.

    // -- HUD (top bar) ---------------------------------------------------------
    var hud = document.createElement("div");
    css(hud, {
      position:"absolute", top:"0", left:"0", right:"0", zIndex:"1000",
      display:"flex", alignItems:"center", justifyContent:"space-between",
      background:"rgba(0,0,0,0.65)", color:"#fff", fontFamily:"sans-serif",
      fontSize:"2vmin", fontWeight:"600", padding:"1vmin 2vmin",
      pointerEvents:"none", gap:"2vmin", boxSizing:"border-box"
    });
    var locEl = document.createElement("span");
    locEl.textContent = "\uD83D\uDCE1  NOAA NEXRAD \u2014 " + label;
    var timeEl = document.createElement("span");
    css(timeEl, { opacity:"0.85", fontSize:"1.8vmin", textAlign:"right", whiteSpace:"nowrap" });
    hud.appendChild(locEl);
    hud.appendChild(timeEl);
    root.appendChild(hud);

    // -- Scrubber (bottom bar) -------------------------------------------------
    var scrubBar = document.createElement("div");
    css(scrubBar, {
      position:"absolute", bottom:"0", left:"0", right:"0", zIndex:"1000",
      background:"rgba(0,0,0,0.60)", display:"flex", gap:"0.4vmin",
      padding:"0.9vmin 8vmin 0.9vmin 2vmin", alignItems:"center", boxSizing:"border-box"
    });
    var ticks = frames.map(function(f, i) {
      var tick = document.createElement("div");
      css(tick, {
        flex:"1", height:"0.7vmin", borderRadius:"0.4vmin",
        background:"rgba(255,255,255,0.18)", cursor:"pointer", transition:"background 0.15s"
      });
      tick.addEventListener("click", function() { stopPlay(); showFrame(i); });
      scrubBar.appendChild(tick);
      return tick;
    });
    root.appendChild(scrubBar);

    // -- Play/Pause button (bottom-right, above scrubber) ----------------------
    var playBtn = document.createElement("button");
    css(playBtn, {
      position:"absolute", bottom:"0.4vmin", right:"1.5vmin", zIndex:"1001",
      background:"rgba(0,0,0,0.72)", color:"#fff",
      border:"2px solid rgba(255,255,255,0.45)", borderRadius:"50%",
      width:"5.5vmin", height:"5.5vmin", fontSize:"2.6vmin", cursor:"pointer",
      display:"flex", alignItems:"center", justifyContent:"center",
      lineHeight:"1", padding:"0", transition:"background 0.2s"
    });
    playBtn.textContent = "▶";
    playBtn.addEventListener("click", function() { playing ? stopPlay() : startPlay(); });
    playBtn.addEventListener("mouseenter", function() { playBtn.style.background = "rgba(255,255,255,0.22)"; });
    playBtn.addEventListener("mouseleave", function() { playBtn.style.background = "rgba(0,0,0,0.72)"; });
    root.appendChild(playBtn);

    // -- UI update (HUD + scrubber ticks) --------------------------------------
    function updateUI() {
      var f = frames[currentFrame];
      timeEl.textContent = "Frame " + (currentFrame + 1) + "/" + frames.length + "   " + fmtUnix(f ? f.time : 0);
      ticks.forEach(function(tick, i) {
        tick.style.background = i === currentFrame
          ? "#4fc3f7"
          : i < currentFrame ? "rgba(255,255,255,0.45)" : "rgba(255,255,255,0.18)";
      });
    }

    // Now safe to render the first frame and start the animation loop.
    showFrame(radarLayers.length - 1);
    setTimeout(startPlay, 1200);

    // -- Auto-refresh ----------------------------------------------------------
    async function refreshFrames() {
      if (window.SIGNAGE_OFFLINE) return; // skip when offline
      console.log("[WeatherRadar] Refreshing radar frames...");
      var newFrames = await fetchRainViewerFrames();
      newFrames.forEach(function(f, i) {
        if (radarLayers[i]) {
          radarLayers[i].setUrl(RV_TILE_TPL.replace("{path}", f.path));
          frames[i] = f;
        }
      });
    }
    if (cfg.refresh_minutes > 0) {
      setInterval(refreshFrames, cfg.refresh_minutes * 60 * 1000);
    }
    // Refresh immediately when server comes back online
    window.addEventListener('signage:online_changed', (e) => {
      if (!e.detail.offline) refreshFrames();
    });
  }

  // -- Main entry point --------------------------------------------------------
  async function init() {
    if (!cfg.location) {
      showMessage("No location set. Open plugin settings and enter a zip code, city name, or lat,lon.", true);
      return;
    }

    showMessage("Locating...", false);
    var parsed = parseLocation(cfg.location);
    if (!parsed) { showMessage("Could not parse location. Use a zip, city name, or lat,lon.", true); return; }

    var lat, lon, label;
    if (parsed.lat != null) {
      lat = parsed.lat; lon = parsed.lon; label = parsed.label;
    } else if (parsed.zip) {
      showMessage("Locating zip " + parsed.zip + "...", false);
      var gz = await geocodeZip(parsed.zip);
      if (!gz) { showMessage("Unknown zip code: " + parsed.zip, true); return; }
      lat = gz.lat; lon = gz.lon; label = gz.label;
    } else {
      showMessage("Locating \"" + parsed.query + "\"...", false);
      var gc = await geocodeCity(parsed.query);
      if (!gc) { showMessage("Could not find: " + parsed.query + ". Try lat,lon instead.", true); return; }
      lat = gc.lat; lon = gc.lon; label = gc.label;
    }

    showMessage("", false);

    var frames = await fetchRainViewerFrames();
    if (!frames.length) {
      // Differentiate the offline case so signage admins know it's not
      // a configuration problem -- the radar simply needs internet to
      // pull live tiles. Plugin will retry on its normal refresh
      // interval; in the meantime advance to the next slide so the
      // playlist doesn't freeze on a "Check network connection" page.
      var msg = (window.signageCache && window.signageCache.isOffline())
        ? "Radar unavailable offline. Tiles need a live network."
        : "Could not load radar data from RainViewer. Check network connection.";
      showMessage(msg, true);
      // Signal the parent so the slide advances after a short delay.
      setTimeout(function () {
        try { window.parent.postMessage({ type: 'signage:complete' }, '*'); } catch (_) {}
      }, 2500);
      return;
    }
    console.log('[WeatherRadar] Loaded ' + frames.length + ' frames from RainViewer.');

    await loadLeaflet();
    buildMap(lat, lon, label, frames);
  }

  init();

  // Advance to next playlist item after duration, unless looping
  const _radarCfg = window.PLUGIN_CONFIG || {};
  const _shouldLoop = !!_radarCfg.loop;
  const _duration   = Math.max(1, parseInt(_radarCfg.duration || 30, 10));
  if (!_shouldLoop) {
    setTimeout(() => {
      try { window.parent.postMessage({ type: 'signage:complete' }, '*'); } catch {}
    }, _duration * 1000);
  }
})();
