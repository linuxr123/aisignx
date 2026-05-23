(function () {
  const cfg = (window.PLUGIN_CONFIG || {});
  const root = document.getElementById('root') || document.body;

  document.documentElement.style.height = '100%';
  document.documentElement.style.background = '#000';
  document.body.style.height = '100%';
  document.body.style.margin = '0';
  document.body.style.background = '#000';

  root.style.display = 'flex';
  root.style.alignItems = 'center';
  root.style.justifyContent = 'center';
  root.style.width  = '100vw';
  root.style.height = '100vh';
  root.style.background = '#000';
  root.style.color = '#fff';
  root.style.cursor = 'none';

  root.innerHTML = `
    <div id="yt-player" style="width:100%;height:100%;"></div>
    <div id="yt-loading" style="position:absolute;top:0;left:0;right:0;bottom:0;
         display:none;align-items:center;justify-content:center;font:16px/1.4 sans-serif;color:transparent;background:#000;">
    </div>
    <div id="yt-error" style="position:absolute;top:0;left:0;right:0;bottom:0;
         display:none;align-items:center;justify-content:center;font:16px/1.4 sans-serif;color:#f66;background:#000;">
      <span id="yt-error-msg">Unable to load YouTube video</span>
    </div>
  `;

  const loadingEl = document.getElementById('yt-loading');
  const errorEl   = document.getElementById('yt-error');
  const errorMsgEl= document.getElementById('yt-error-msg');

  function showError(msg) {
    if (loadingEl) loadingEl.style.display = 'none';
    if (errorEl) errorEl.style.display = 'flex';
    if (errorMsgEl) errorMsgEl.textContent = msg || 'Error';
    console.error('[YouTube Plugin]', msg || '(no message)');
  }

  function extractVideoId(input) {
    if (!input) return null;
    const s = String(input).trim();
    if (/^[A-Za-z0-9_-]{11}$/.test(s)) return s;
    try {
      const u = new URL(s, window.location.origin);
      if (/^((www|m)\.)?youtu\.be$/i.test(u.hostname)) {
        const id = u.pathname.split('/').filter(Boolean)[0];
        if (id && /^[A-Za-z0-9_-]{11}$/.test(id)) return id;
      }
      if (/^((www|m)\.)?youtube\.com$/i.test(u.hostname)) {
        if (u.pathname.startsWith('/watch')) {
          const v = u.searchParams.get('v');
          if (v && /^[A-Za-z0-9_-]{11}$/.test(v)) return v;
        }
        if (u.pathname.startsWith('/embed/') || u.pathname.startsWith('/shorts/')) {
          const id = u.pathname.split('/').pop();
          if (id && /^[A-Za-z0-9_-]{11}$/.test(id)) return id;
        }
      }
    } catch {}
    return null;
  }

  function parseVideoList() {
    const videoList = [];
    if (cfg.videos) {
      const lines = String(cfg.videos).split(/[\r\n]+/).map(l => l.trim()).filter(Boolean);
      for (const line of lines) {
        // Format: URL_or_ID [start_seconds] [end_seconds]
        const parts = line.split(/\s+/);
        const id = extractVideoId(parts[0]);
        if (!id) continue;
        const start = parts.length > 1 ? Math.max(0, parseInt(parts[1], 10) || 0) : null;
        const end   = parts.length > 2 ? Math.max(0, parseInt(parts[2], 10) || 0) : null;
        videoList.push({ id, start, end });
      }
    }
    if (videoList.length === 0 && cfg.video) {
      const id = extractVideoId(cfg.video);
      if (id) videoList.push({ id, start: null, end: null });
    }
    return videoList;
  }

  const videoList = parseVideoList();
  if (videoList.length === 0) {
    showError('Invalid or missing YouTube URL/ID in plugin settings.');
    return;
  }

  const STORAGE_KEY = 'youtube_plugin_last_video_index';
  
  function getNextVideoIndex(mode) {
    const total = videoList.length;
    if (total === 1) return 0;
    if (mode === 'random') {
      return Math.floor(Math.random() * total);
    } else {
      let lastIndex = -1;
      try {
        const stored = sessionStorage.getItem(STORAGE_KEY);
        if (stored !== null) lastIndex = parseInt(stored, 10);
      } catch (e) {
        console.warn('sessionStorage not available:', e);
      }
      const nextIndex = (lastIndex + 1) % total;
      try {
        sessionStorage.setItem(STORAGE_KEY, String(nextIndex));
      } catch (e) {
        console.warn('Cannot write to sessionStorage:', e);
      }
      return nextIndex;
    }
  }

  function loadYTAPI() {
    if (window._YT_API_PROMISE) return window._YT_API_PROMISE;
    window._YT_API_PROMISE = new Promise((resolve, reject) => {
      if (window.YT && window.YT.Player) {
        resolve(); return;
      }
      const tag = document.createElement('script');
      tag.src = 'https://www.youtube.com/iframe_api';
      tag.async = true;
      tag.onerror = () => reject(new Error('Failed to load YouTube API script'));
      const firstScriptTag = document.getElementsByTagName('script')[0];
      const parent = (firstScriptTag && firstScriptTag.parentNode) || document.head || document.body;
      parent.insertBefore(tag, firstScriptTag);
      const prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = function () {
        try { if (typeof prev === 'function') prev(); } catch {}
        if (window.YT && window.YT.Player) resolve();
      };
      const chk = setInterval(() => {
        if (window.YT && window.YT.Player) { clearInterval(chk); resolve(); }
      }, 50);
      // Hard timeout. The browser's TCP timeout for a totally-unreachable
      // host can be 30+ seconds, during which the playlist would freeze
      // on this slide. 6s is generous for a working network and short
      // enough that an offline kiosk skips the slide quickly.
      setTimeout(() => {
        clearInterval(chk);
        if (!(window.YT && window.YT.Player)) {
          reject(new Error('YouTube API load timed out (offline?)'));
        }
      }, 6000);
    });
    return window._YT_API_PROMISE;
  }

  function buildPlayerVars(entry) {
    const videoId = entry.id;
    // Per-video start/end override global defaults
    const globalStart = Math.max(0, parseInt(cfg.start || 0, 10) || 0);
    const globalEnd   = Math.max(0, parseInt(cfg.end   || 0, 10) || 0);
    const start = entry.start !== null ? entry.start : globalStart;
    const end   = entry.end   !== null ? entry.end   : globalEnd;
    const loop     = !!cfg.loop;
    const muted    = cfg.mute !== false;
    const captions = !!cfg.cc;
    const controls = cfg.controls === true ? 1 : 0;
    const rel      = cfg.rel === true ? 1 : 0;
    const branding = cfg.branding !== false ? 1 : 0;
    const privacy  = cfg.privacy_mode !== false;
    const vars = {
      autoplay: 1,
      playsinline: 1,
      fs: 1,
      disablekb: 1,
      controls,
      rel,
      modestbranding: branding,
      iv_load_policy: 3,
      cc_load_policy: captions ? 1 : 0,
      start: start || undefined,
      end: end > 0 ? end : undefined,
      // loop requires playlist to be set to the same videoId to work in the IFrame API.
      // When loop is OFF we omit both so YouTube never auto-replays.
      ...(loop ? { loop: 1, playlist: videoId } : { loop: 0 }),
      origin: window.location.origin,
      enablejsapi: 1
    };
    return { vars, muted, privacy, startTime: start, endTime: end };
  }

  const requestedQualityRaw = (cfg.quality != null ? cfg.quality : cfg.resolution);

  function getYouTubeQuality(requested) {
    if (!requested) return 'default';
    const q = String(requested).toLowerCase();
    const native = ['default','small','medium','large','hd720','hd1080','hd1440','hd2160','highres'];
    if (native.includes(q)) return q;
    const map = {
      '144p': 'small',  '240p': 'small',
      '360p': 'medium', '480p': 'large',
      '720p': 'hd720',  '1080p': 'hd1080',
      '1440p': 'hd1440','2160p': 'hd2160',
      '4k': 'hd2160'
    };
    return map[q] || 'default';
  }

  const targetQuality = getYouTubeQuality(requestedQualityRaw);

  function applyPlaybackRate(player) {
    if (cfg.playback_rate !== undefined && cfg.playback_rate !== null && cfg.playback_rate !== '') {
      const desired = Number(cfg.playback_rate);
      if (!Number.isNaN(desired) && typeof player.setPlaybackRate === 'function') {
        const allowed = (typeof player.getAvailablePlaybackRates === 'function')
          ? player.getAvailablePlaybackRates()
          : [0.25, 0.5, 1, 1.25, 1.5, 2];
        if (Array.isArray(allowed) && allowed.includes(desired)) {
          try { player.setPlaybackRate(desired); } catch {}
        }
      }
    }
  }

  let qualityRetryTimer = null;
  function startQualityEnforcement(player, opts = {}) {
    if (!targetQuality || targetQuality === 'default') return;
    const maxMs = opts.maxMs || 3000;
    const step  = opts.step  || 200;
    const t0    = Date.now();
    function tick() {
      try {
        const cur = (typeof player.getPlaybackQuality === 'function')
          ? player.getPlaybackQuality()
          : null;
        if (cur !== targetQuality && typeof player.setPlaybackQuality === 'function') {
          player.setPlaybackQuality(targetQuality);
        }
        if (cur === targetQuality || (Date.now() - t0) >= maxMs) {
          clearInterval(qualityRetryTimer);
          qualityRetryTimer = null;
          return;
        }
      } catch {}
    }
    if (qualityRetryTimer) clearInterval(qualityRetryTimer);
    qualityRetryTimer = setInterval(tick, step);
  }

  let player = null;
  let timeMonitor = null;
  let durationTimer = null;
  let currentVideoIndex = -1;
  let playlistActive = false;
  let videosPlayed = 0;
  let _hasPlayed = false;  // true once the first PLAYING state fires
  const videoDuration = Math.max(0, parseInt(cfg.video_duration || 0, 10) || 0);
  const playbackMode = cfg.playback_mode || 'sequential';

  function clearAllTimers() {
    if (timeMonitor) {
      clearInterval(timeMonitor);
      timeMonitor = null;
    }
    if (durationTimer) {
      clearTimeout(durationTimer);
      durationTimer = null;
    }
    if (qualityRetryTimer) {
      clearInterval(qualityRetryTimer);
      qualityRetryTimer = null;
    }
  }

  function stopPlayer() {
    clearAllTimers();
    if (player && typeof player.stopVideo === 'function') {
      try {
        player.stopVideo();
      } catch (e) {
        console.warn('Error stopping video:', e);
      }
    }
  }

  function startTimeMonitoring(playerInstance, startTime, endTime, shouldLoop) {
    if (timeMonitor) clearInterval(timeMonitor);
    if (endTime <= 0) return;
    timeMonitor = setInterval(() => {
      try {
        if (!playerInstance.getCurrentTime || !playerInstance.getPlayerState) return;
        const t = playerInstance.getCurrentTime();
        const state = playerInstance.getPlayerState();
        if (state === YT.PlayerState.PLAYING && t >= (endTime - 0.1)) {
          if (shouldLoop) {
            playerInstance.seekTo(startTime || 0, true);
            playerInstance.playVideo();
          } else {
            if (playlistActive && videoList.length > 1) {
              playNextVideo();
            } else {
              stopPlayer();
              signalComplete();
            }
          }
        }
      } catch (e) {
        console.warn('Time monitoring error:', e);
      }
    }, 200);
  }

  function playNextVideo() {
    if (videoList.length <= 1) return;
    videosPlayed++;
    const shouldLoop = !!cfg.loop;
    // If not looping and we've played all videos, signal complete
    if (!shouldLoop && videosPlayed >= videoList.length) {
      stopPlayer();
      signalComplete();
      return;
    }
    currentVideoIndex = getNextVideoIndex(playbackMode);
    const entry = videoList[currentVideoIndex];
    _hasPlayed = false;  // reset for the incoming video
    console.log('[YouTube Plugin] Playing next video:', entry.id, 'Index:', currentVideoIndex);
    if (player && typeof player.loadVideoById === 'function') {
      const { startTime, endTime } = buildPlayerVars(entry);
      try {
        player.loadVideoById({
          videoId: entry.id,
          startSeconds: startTime || 0
        });
        if (endTime > 0) {
          startTimeMonitoring(player, startTime, endTime, !!cfg.loop);
        }
        if (videoDuration > 0 && playlistActive) {
          if (durationTimer) clearTimeout(durationTimer);
          durationTimer = setTimeout(() => {
            playNextVideo();
          }, videoDuration * 1000);
        }
      } catch (e) {
        console.error('Error loading next video:', e);
      }
    }
  }

  function createPlayer() {
    currentVideoIndex = getNextVideoIndex(playbackMode);
    videosPlayed = 0;
    // playlistActive = timed rotation (video_duration set). Without it we play
    // each video fully in sequence and signal complete after the last one.
    playlistActive = videoDuration > 0 && videoList.length > 1;
    const entry = videoList[currentVideoIndex];
    console.log('[YouTube Plugin] Starting video:', entry.id, 'Index:', currentVideoIndex, 'Playlist active:', playlistActive);
    const { vars, muted, privacy, startTime, endTime } = buildPlayerVars(entry);
    const host = privacy ? 'https://www.youtube-nocookie.com' : undefined;
    const shouldLoop = !!cfg.loop;
    try {
      player = new YT.Player('yt-player', {
        height: '100%',
        width: '100%',
        host,
        videoId: entry.id,
        playerVars: vars,
        events: {
          onReady: (ev) => {
            try {
              if (muted && ev.target.mute) ev.target.mute();
              if (ev.target.playVideo) ev.target.playVideo();
              applyPlaybackRate(ev.target);
              startQualityEnforcement(ev.target, { maxMs: 1500, step: 200 });
              if (loadingEl) loadingEl.style.display = 'none';
              if (endTime > 0) {
                startTimeMonitoring(ev.target, startTime, endTime, shouldLoop);
              }
              if (videoDuration > 0 && playlistActive) {
                if (durationTimer) clearTimeout(durationTimer);
                durationTimer = setTimeout(() => {
                  playNextVideo();
                }, videoDuration * 1000);
              }
            } catch (e) {
              console.warn('onReady handler error:', e);
            }
          },
          onStateChange: (ev) => {
            if (ev.data === YT.PlayerState.PLAYING) {
              if (loadingEl) loadingEl.style.display = 'none';
              applyPlaybackRate(ev.target);
              startQualityEnforcement(ev.target, { maxMs: 2000, step: 200 });
            }
            if (ev.data === YT.PlayerState.BUFFERING) {
              startQualityEnforcement(ev.target, { maxMs: 1500, step: 200 });
            }
            if (ev.data === YT.PlayerState.ENDED) {
              if (shouldLoop && !playlistActive) {
                try {
                  ev.target.seekTo(startTime || 0, true);
                  ev.target.playVideo();
                } catch {}
              } else if (videoList.length > 1) {
                // multi-video: playNextVideo decides whether to continue or complete
                playNextVideo();
              } else {
                // single video, no loop
                try { ev.target.stopVideo(); } catch {}
                stopPlayer();
                signalComplete();
              }
            }
            // YT sometimes fires UNSTARTED (-1) when it tries to loop a single
            // video without the playlist param. Only act on it for single-video
            // non-loop mode AND only after the video has actually played at least
            // once (guard with a flag so loadVideoById transitions don't trigger it).
            if (ev.data === YT.PlayerState.UNSTARTED && !shouldLoop && videoList.length === 1 && _hasPlayed) {
              try { ev.target.stopVideo(); } catch {}
              stopPlayer();
              signalComplete();
            }
            if (ev.data === YT.PlayerState.PLAYING) _hasPlayed = true;
          },
          onError: (ev) => {
            clearAllTimers();
            let msg = 'YouTube reported an error.';
            if (ev && typeof ev.data === 'number') {
              const M = {
                2:   'Invalid parameter. Check the video ID/URL.',
                5:   'HTML5 player error.',
                100: 'Video not found or removed.',
                101: 'Embedding disabled by the video owner.',
                150: 'Embedding not allowed by the video owner.'
              };
              msg = M[ev.data] || msg;
            }
            if (playlistActive && videoList.length > 1) {
              console.warn('[YouTube Plugin] Error with current video, trying next...', msg);
              setTimeout(() => playNextVideo(), 1000);
            } else {
              showError(msg);
            }
          }
        }
      });
    } catch (e) {
      showError('Failed to initialize YouTube player: ' + e.message);
    }
  }

  window.addEventListener('beforeunload', () => {
    stopPlayer();
  });

  // Tell the parent display player to advance to the next playlist item.
  function signalComplete() {
    try { window.parent.postMessage({ type: 'signage:complete' }, '*'); } catch {}
  }

  loadYTAPI()
    .then(() => createPlayer())
    .catch((err) => {
      // Show the error briefly then signal completion so the playlist
      // moves on to the next slide instead of freezing here.
      showError('Could not load YouTube API. Check internet connection. ' + err.message);
      setTimeout(signalComplete, 1500);
    });

  window.addEventListener('resize', () => {});
})();
