/**
 * display_player.js
 * =================
 * Clientless browser-based digital signage player.
 *
 * Responsibilities:
 *  - Render image / video / webpage (iframe) playlist items in sequence
 *  - Listen on SSE /display/<token>/events for instant reload pushes
 *  - POST keepalive pings to /display/<token>/ping every 60 s
 *  - Enforce single-client: on 409 or SSE disconnect event → show blocked UI
 *  - Auto-reconnect SSE with exponential backoff on network drops
 *  - Smooth cross-fade transitions between slides
 */

(() => {
    'use strict';

    /* -----------------------------------------------------------------------
     * Config — injected by display_player.html
     * --------------------------------------------------------------------- */
    const TOKEN       = window.DISPLAY_TOKEN;
    const BOOT        = window.BOOT_PLAYLIST;   // null or full playlist object
    const PING_EVERY  = 15_000;                 // ms between pings
    const SSE_BASE    = 1_000;                  // SSE reconnect base delay ms
    const SSE_MAX     = 30_000;                 // SSE reconnect cap ms
    const CACHE_KEY   = `signage_playlist_${TOKEN}`;  // localStorage key
    const JS_VERSION  = 'v30';                  // bump when display_player.js changes
    const BLACK_VIDEO_POSTER = 'data:image/svg+xml;charset=utf-8,%3Csvg xmlns="http://www.w3.org/2000/svg" width="16" height="9" viewBox="0 0 16 9"%3E%3Crect width="16" height="9" fill="%23000"/%3E%3C/svg%3E';
    const CLIENT_ID   = getClientInstanceId();
    let SHOW_MEDIA_BUTTONS = !!window.SHOW_MEDIA_BUTTONS;
    let ALLOW_INPUT        = !!window.ALLOW_INPUT;
    let SYNC_PLAYBACK_OPT_OUT = !!window.SYNC_PLAYBACK_OPT_OUT;
    let UNLOCK_PIN         = String(window.UNLOCK_PIN || '');
    // Master display volume (0-100). The server already resolves the
    // "audio_enabled" decision for each video item by combining the
    // per-item mute, the playlist video_audio_default, and the per-media
    // default. We just multiply that boolean by VOLUME/100 here.
    let VOLUME = (window.VOLUME == null) ? 100 : Math.max(0, Math.min(100, parseInt(window.VOLUME, 10) || 0));

    function getClientInstanceId() {
        const injected = String(window.DISPLAY_CLIENT_ID || '').trim();
        if (injected) return injected.slice(0, 128);
        try {
            const key = 'aisignx_client_instance_id';
            let id = localStorage.getItem(key);
            if (!id) {
                id = (typeof crypto !== 'undefined' && crypto.randomUUID)
                    ? crypto.randomUUID()
                    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
                localStorage.setItem(key, id);
            }
            return String(id).slice(0, 128);
        } catch {
            return '';
        }
    }

    function clientQuery() {
        return CLIENT_ID ? `client_id=${encodeURIComponent(CLIENT_ID)}` : '';
    }

    // PIN unlock state. When a PIN is configured the player is LOCKED by
    // default -- pointer/keyboard events are swallowed until the admin
    // taps and types the correct PIN, which grants UNLOCK_GRACE_MS of
    // pass-through input. UNLOCK_GRACE_MS is intentionally short (5 min)
    // so a forgotten unlock automatically re-locks.
    const UNLOCK_GRACE_MS    = 5 * 60_000;
    const PIN_FAIL_LOCKOUT   = 5;          // wrong tries before lockout
    const PIN_LOCKOUT_MS     = 60_000;     // lockout duration
    let _unlockedUntil       = 0;          // ms-since-epoch
    let _pinFailCount        = 0;
    let _pinLockedOutUntil   = 0;

    // Tell the service worker what JS version this tab is running.
    // If the SW activates a newer version it will reload the tab automatically,
    // but this also lets the server detect stale clients via ping if needed.
    if (navigator.serviceWorker && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ type: 'client_version', version: JS_VERSION });
    }
    // When the SW takes control (after an update), reload so we get fresh JS
    navigator.serviceWorker && navigator.serviceWorker.addEventListener('controllerchange', () => {
        window.location.reload();
    });

    // Server reachability tracker — declared early so applySettings can read it.
    // Three connectivity states are tracked:
    //   'online'           = our server is reachable
    //   'server-offline'   = our server unreachable, but the wider internet works
    //   'network-offline'  = no internet at all
    // _serverOffline is kept around for backwards compatibility with older code
    // that just wants a boolean. Plugins receive the full state via the
    // 'signage:online_state' postMessage event.
    let _serverOffline   = false;
    let _networkState    = 'online';   // 'online' | 'server-offline' | 'network-offline'
    window.SIGNAGE_OFFLINE      = false;
    window.SIGNAGE_NETWORK_STATE = 'online';

    /* -----------------------------------------------------------------------
     * Input lock + PIN unlock
     *
     * Effective lock rule (checked at every event by isLocked()):
     *   * If ALLOW_INPUT is true            -> unlocked (admin override)
     *   * Else if UNLOCK_PIN is empty       -> locked  (legacy lock-down)
     *   * Else                              -> locked unless _unlockedUntil > now
     *
     * "Allow keyboard & mouse input" in the display settings is the
     * top-level switch; if it's on the kiosk is fully open and the PIN
     * is irrelevant. If it's off and a PIN is set, the kiosk is locked
     * by default and a 1.5-second long-press pops the PIN keypad. If
     * it's off and no PIN is set, the kiosk is hard-locked with no
     * unlock path (matches the old pre-PIN behaviour).
     *
     * Listeners are always attached; they consult isLocked() at fire
     * time so live settings changes (PIN added/removed via SSE) take
     * effect immediately without page reload.
     * --------------------------------------------------------------------- */
    function isLocked() {
        if (ALLOW_INPUT) return false;
        if (UNLOCK_PIN) {
            return Date.now() >= _unlockedUntil;
        }
        return true;
    }

    function isPinLockedOut() {
        return Date.now() < _pinLockedOutUntil;
    }

    /** Called whenever a tap/click hits the kiosk while locked. Pops the
     *  PIN keypad up over the playlist. Subsequent events are still
     *  swallowed by the global handlers below until the PIN is correct. */
    function promptForPin() {
        if (!UNLOCK_PIN) return;       // no PIN set -> nothing to prompt
        if (isPinLockedOut()) {
            showPinKeypad('Too many wrong tries. Try again in ' +
                          Math.ceil((_pinLockedOutUntil - Date.now()) / 1000) +
                          's.');
            return;
        }
        showPinKeypad(null);
    }
    window.AISignXPromptUnlock = promptForPin;

    /**
     * Android TV / Shield remote keys are handled in PlayerActivity and
     * forwarded here so WebView cannot pass them through to <video> seeking.
     */
    window.AISignXTvKey = function (action) {
        const a = String(action || '');
        if (a === 'menu') {
            promptForPin();
            return;
        }
        if (a === 'back') {
            if (_pinKeypadOpen) closePinKeypad();
            return;
        }
        if (a.startsWith('digit:')) {
            const d = a.slice(6);
            if (!UNLOCK_PIN || d.length !== 1 || d < '0' || d > '9') return;
            if (!_pinKeypadOpen) showPinKeypad(null);
            onPinDigit(d);
            return;
        }
        if (a === 'center') {
            if (_pinKeypadOpen) {
                if (!activateFocusedPinButton()) onPinSubmit();
            } else if (isLocked() && UNLOCK_PIN) {
                promptForPin();
            }
            return;
        }
        if (isLocked() || _pinKeypadOpen) {
            if (UNLOCK_PIN && !_pinKeypadOpen) promptForPin();
            return;
        }
        if (a === 'next' || a === 'prev' || a === 'ffwd' || a === 'rewind' || a === 'playpause') {
            if (a === 'next')      { manualNext();    showMediaButtons(); }
            if (a === 'prev')      { goBack();        showMediaButtons(); }
            if (a === 'ffwd')      { scrubVideo(+10); showMediaButtons(); }
            if (a === 'rewind')    { scrubVideo(-10); showMediaButtons(); }
            if (a === 'playpause') { togglePause(); }
        }
    };

    // ── Shell minimize bridge (Electron / Android) ───────────────────────────
    // After a correct PIN we ask the native shell to minimize the kiosk so a
    // technician can use the underlying desktop. The shell owns the "restore
    // on idle / on user return" policy and signals us to re-lock via the
    // relock callback below. forceRelock() drops the unlock grace immediately.
    function requestShellMinimize() {
        try {
            if (window.signage && typeof window.signage.unlockMinimize === 'function') {
                window.signage.unlockMinimize();
                return true;
            }
            if (window.AISignXNative && typeof window.AISignXNative.unlockMinimize === 'function') {
                window.AISignXNative.unlockMinimize();
                return true;
            }
        } catch (_) {}
        return false;
    }
    function forceRelock() {
        _unlockedUntil = 0;
        try { applyInputLock(); } catch (_) {}
        if (activeSlide) {
            try {
                activeSlide.querySelectorAll('iframe').forEach(f => { f.tabIndex = -1; });
            } catch (_) {}
        }
        if (_pinKeypadOpen) { try { closePinKeypad(); } catch (_) {} }
    }
    // Expose so the Android shell can call back into the page on restore.
    window.AISignXRelock = forceRelock;
    try {
        if (window.signage && typeof window.signage.onRelock === 'function') {
            window.signage.onRelock(() => {
                forceRelock();
                _pingFailStreak = 0;
                ping();
                resumePlayback();
            });
        }
    } catch (_) {}

    function _isMediaControlKey(e) {
        const k = e.key;
        return (
            k === 'ArrowLeft' || k === 'ArrowRight' || k === 'ArrowUp' || k === 'ArrowDown' ||
            k === ' ' || k === 'MediaPlayPause' || k === 'MediaTrackNext' ||
            k === 'MediaTrackPrevious' || k === 'FastForward' || k === 'Rewind'
        );
    }

    function _blockKey(e) {
        if (!isLocked()) return;
        if (_pinKeypadOpen) {
            if (_isMediaControlKey(e)) {
                e.preventDefault();
                e.stopPropagation();
            }
            return;
        }
        if (UNLOCK_PIN && e.type === 'keydown') {
            const openPin = (
                e.key === 'Enter' || e.key === 'Select' || e.key === '*' ||
                e.key === 'Multiply' || e.code === 'NumpadEnter' ||
                e.key === 'BrowserSearch' || e.key === 'Info' ||
                (e.key >= '0' && e.key <= '9')
            );
            if (openPin) {
                e.preventDefault();
                e.stopPropagation();
                if (e.key >= '0' && e.key <= '9') {
                    showPinKeypad(null);
                    onPinDigit(e.key);
                } else if (!_pinKeypadOpen) {
                    promptForPin();
                }
                return;
            }
        }
        if (_isMediaControlKey(e)) {
            e.preventDefault();
            e.stopPropagation();
            if (UNLOCK_PIN && !_pinKeypadOpen) promptForPin();
            return;
        }
        e.preventDefault();
    }
    window.addEventListener('keydown',  _blockKey, true);
    window.addEventListener('keyup',    _blockKey, true);
    window.addEventListener('keypress', _blockKey, true);
    window.addEventListener('contextmenu', e => { if (isLocked()) e.preventDefault(); }, true);

    // Long-press to prompt for PIN. We listen on window for pointerdown so
    // ANY long press anywhere on the kiosk pops the keypad -- but a normal
    // tap (release < LONGPRESS_MS) does NOT, so the on-screen media skip
    // buttons remain usable when the admin has unlocked input. Also
    // explicitly ignore presses that originate inside #media-buttons or
    // #media-btn-trap so the buttons are always click-through-friendly.
    const LONGPRESS_MS = 1500;
    let _lpTimer    = null;
    let _lpStartXY  = null;
    function _isInMediaBtns(target) {
        try {
            return !!(target && target.closest && target.closest('#media-buttons'));
        } catch (_) { return false; }
    }
    function _maybePromptOnLongPress(e) {
        if (!UNLOCK_PIN) return;
        if (!isLocked()) { console.log('[lock] longpress ignored: not locked'); return; }
        if (_pinKeypadOpen) { console.log('[lock] longpress ignored: keypad open'); return; }
        // Don't arm the long-press timer for clicks on media-skip buttons.
        if (_isInMediaBtns(e.target)) { console.log('[lock] longpress ignored: media btn'); return; }
        const p = eventPoint(e);
        _lpStartXY = p;
        if (_lpTimer) clearTimeout(_lpTimer);
        console.log('[lock] longpress armed at', p.x, p.y, 'target=', e.target && e.target.tagName);
        _lpTimer = setTimeout(() => {
            _lpTimer = null;
            console.log('[lock] longpress fired -> promptForPin');
            promptForPin();
        }, LONGPRESS_MS);
    }
    function eventPoint(e) {
        const t = (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0]) || e;
        return { x: t.clientX || 0, y: t.clientY || 0 };
    }
    function _cancelLongPress(e) {
        // pointerup / pointercancel always cancel the timer.
        // pointermove only cancels if the finger has actually moved more
        // than 12px from the starting point -- a touch screen reports
        // micro-movement on every frame even when the user is holding
        // perfectly still, so an unconditional cancel here would prevent
        // the long-press from EVER firing on Android.
        if (!e || e.type === 'pointerup' || e.type === 'pointercancel') {
            if (_lpTimer) { clearTimeout(_lpTimer); _lpTimer = null; }
            _lpStartXY = null;
            return;
        }
        if ((e.type === 'pointermove' || e.type === 'touchmove' || e.type === 'mousemove') && _lpStartXY) {
            const p = eventPoint(e);
            const dx = Math.abs(p.x - _lpStartXY.x);
            const dy = Math.abs(p.y - _lpStartXY.y);
            if (dx > 12 || dy > 12) {
                if (_lpTimer) { clearTimeout(_lpTimer); _lpTimer = null; }
                _lpStartXY = null;
            }
        }
    }
    window.addEventListener('pointerdown',   _maybePromptOnLongPress, true);
    window.addEventListener('pointerup',     _cancelLongPress, true);
    window.addEventListener('pointercancel', _cancelLongPress, true);
    window.addEventListener('pointermove',   _cancelLongPress, true);
    window.addEventListener('touchstart',    _maybePromptOnLongPress, { capture: true, passive: true });
    window.addEventListener('touchend',      _cancelLongPress, true);
    window.addEventListener('touchcancel',   _cancelLongPress, true);
    window.addEventListener('touchmove',     _cancelLongPress, { capture: true, passive: true });
    window.addEventListener('mousedown',     _maybePromptOnLongPress, true);
    window.addEventListener('mouseup',       _cancelLongPress, true);
    window.addEventListener('mousemove',     _cancelLongPress, true);

    function applyInputLock() {
        const locked = isLocked();
        document.documentElement.style.userSelect       = locked ? 'none' : '';
        document.documentElement.style.webkitUserSelect = locked ? 'none' : '';
        if (btnTrap) btnTrap.style.pointerEvents = locked ? 'auto' : 'none';
        // Restore cursor if input is now allowed
        if (!locked && !SHOW_MEDIA_BUTTONS) document.body.style.cursor = '';
    }

    /* -----------------------------------------------------------------------
     * Live settings update — called when SSE pushes a 'settings' event
     * --------------------------------------------------------------------- */
    function applyVideoAudio(vid) {
        // Resolve effective audio for a <video>:
        //   - vid._audioWanted is the server-resolved per-item decision
        //     (per-item mute > playlist override > media default).
        //   - VOLUME is the master display volume (0-100); 0 forces mute.
        const wanted = !!vid._audioWanted && VOLUME > 0;
        vid.muted = !wanted;
        vid.volume = wanted ? (VOLUME / 100) : 0;
    }

    function notifyClientAutoUpdateFlag(enabled) {
        try {
            if (typeof AISignXNative !== 'undefined' && AISignXNative.setDisplayAutoUpdateClient) {
                AISignXNative.setDisplayAutoUpdateClient(!!enabled);
            }
        } catch (_) { /* not Android */ }
        try {
            if (typeof window.signage === 'object' && window.signage &&
                typeof window.signage.setDisplayAutoUpdate === 'function') {
                window.signage.setDisplayAutoUpdate(!!enabled);
            }
        } catch (_) { /* not Electron */ }
    }

    /* -----------------------------------------------------------------------
     * Client diagnostics (opt-in per-display)
     *
     * When window.DIAGNOSTICS_ENABLED is true the player wraps console.*,
     * window.onerror, unhandledrejection and emits its own [sync] / [net]
     * lines into a small ring buffer that's flushed to
     * /api/display/<token>/diagnostics every DIAG_FLUSH_MS.
     *
     * Off by default; toggled live via the SSE settings event. When the
     * server returns 423 (admin disabled it while we still had a queue),
     * we drop the queue and stop capturing until the admin re-enables.
     * --------------------------------------------------------------------- */
    const DIAG_FLUSH_MS = 5_000;
    const DIAG_MAX_QUEUE = 500;
    const DIAG_MAX_MSG_LEN = 4000;
    let DIAGNOSTICS_ENABLED = !!window.DIAGNOSTICS_ENABLED;
    let _diagQueue = [];
    let _diagFlushTimer = null;
    let _diagFlushInflight = false;
    let _diagCapturedConsole = false;
    let _origConsoleLog = console.log.bind(console);
    let _origConsoleInfo = console.info ? console.info.bind(console) : _origConsoleLog;
    let _origConsoleWarn = console.warn ? console.warn.bind(console) : _origConsoleLog;
    let _origConsoleError = console.error ? console.error.bind(console) : _origConsoleLog;

    function _diagStringify(args) {
        try {
            return Array.prototype.map.call(args, a => {
                if (a == null) return String(a);
                if (typeof a === 'string') return a;
                if (a instanceof Error) return (a.stack || a.message || String(a));
                try { return JSON.stringify(a); } catch (_) { return String(a); }
            }).join(' ').slice(0, DIAG_MAX_MSG_LEN);
        } catch (_) {
            return '[unserializable diagnostic]';
        }
    }

    function diagLog(level, source, message, meta) {
        if (!DIAGNOSTICS_ENABLED) return;
        if (_diagQueue.length >= DIAG_MAX_QUEUE) {
            // Drop oldest to keep the buffer bounded.
            _diagQueue.splice(0, _diagQueue.length - DIAG_MAX_QUEUE + 1);
        }
        _diagQueue.push({
            level:     level,
            source:    source,
            message:   typeof message === 'string' ? message.slice(0, DIAG_MAX_MSG_LEN) : _diagStringify([message]),
            meta:      meta || null,
            client_ts: new Date().toISOString(),
        });
    }

    function captureConsole() {
        if (_diagCapturedConsole) return;
        _diagCapturedConsole = true;
        console.log   = function () { _origConsoleLog.apply(console, arguments);   diagLog('info',  'console', _diagStringify(arguments)); };
        console.info  = function () { _origConsoleInfo.apply(console, arguments);  diagLog('info',  'console', _diagStringify(arguments)); };
        console.warn  = function () { _origConsoleWarn.apply(console, arguments);  diagLog('warn',  'console', _diagStringify(arguments)); };
        console.error = function () { _origConsoleError.apply(console, arguments); diagLog('error', 'console', _diagStringify(arguments)); };
        window.addEventListener('error', (e) => {
            try {
                diagLog('error', 'onerror', String(e.message || e.error || 'error'), {
                    filename: e.filename || null,
                    lineno:   e.lineno   || null,
                    colno:    e.colno    || null,
                    stack:    e.error && e.error.stack ? String(e.error.stack).slice(0, DIAG_MAX_MSG_LEN) : null,
                });
            } catch (_) {}
        });
        window.addEventListener('unhandledrejection', (e) => {
            try {
                const r = e.reason;
                diagLog('error', 'unhandledrejection',
                        (r && r.message) ? r.message : _diagStringify([r]),
                        { stack: r && r.stack ? String(r.stack).slice(0, DIAG_MAX_MSG_LEN) : null });
            } catch (_) {}
        });
    }

    async function flushDiagnostics() {
        if (_diagFlushInflight) return;
        if (!DIAGNOSTICS_ENABLED) { _diagQueue = []; return; }
        if (_diagQueue.length === 0) return;
        if (_serverOffline) return;          // hold the queue; flush on reconnect
        const batch = _diagQueue.splice(0, _diagQueue.length);
        _diagFlushInflight = true;
        try {
            const r = await fetch(`/api/display/${TOKEN}/diagnostics`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ entries: batch }),
                cache: 'no-store',
            });
            if (r.status === 423) {
                // Server says diagnostics are now disabled for this display.
                // Mirror that locally so we stop capturing right away.
                DIAGNOSTICS_ENABLED = false;
                window.DIAGNOSTICS_ENABLED = false;
                _diagQueue = [];
            } else if (!r.ok) {
                // Requeue at front so we don't lose entries on transient failures
                _diagQueue = batch.concat(_diagQueue).slice(-DIAG_MAX_QUEUE);
            }
        } catch (e) {
            _diagQueue = batch.concat(_diagQueue).slice(-DIAG_MAX_QUEUE);
        } finally {
            _diagFlushInflight = false;
        }
    }

    function startDiagnostics() {
        captureConsole();
        if (_diagFlushTimer) return;
        _diagFlushTimer = setInterval(flushDiagnostics, DIAG_FLUSH_MS);
        diagLog('info', 'diagnostics', 'capture started', {
            ua: navigator.userAgent, screen: `${screen.width}x${screen.height}`,
            tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
            jsv: JS_VERSION,
        });
    }
    function stopDiagnostics() {
        if (_diagFlushTimer) { clearInterval(_diagFlushTimer); _diagFlushTimer = null; }
        _diagQueue = [];
        // We deliberately leave the console wrappers in place; diagLog() now
        // short-circuits on `!DIAGNOSTICS_ENABLED` so they're effectively no-ops.
        // Re-instating the originals would race with anything mid-call.
    }
    if (DIAGNOSTICS_ENABLED) startDiagnostics();
    // Expose for plugins / other modules that want to emit structured events.
    window.signageDiag = diagLog;

    function applySettings(s) {
        if (s.allow_input !== undefined) ALLOW_INPUT = !!s.allow_input;
        if (s.show_media_buttons !== undefined) SHOW_MEDIA_BUTTONS = !!s.show_media_buttons;
        if (s.diagnostics_enabled !== undefined) {
            const want = !!s.diagnostics_enabled;
            if (want !== DIAGNOSTICS_ENABLED) {
                DIAGNOSTICS_ENABLED = want;
                window.DIAGNOSTICS_ENABLED = want;
                if (want) startDiagnostics();
                else      stopDiagnostics();
            }
        }
        if (s.volume !== undefined) {
            const v = Math.max(0, Math.min(100, parseInt(s.volume, 10) || 0));
            if (v !== VOLUME) {
                VOLUME = v;
                // Apply to any currently-playing video right away.
                if (activeSlide) {
                    activeSlide.querySelectorAll('video').forEach(applyVideoAudio);
                }
            }
        }
        if (s.show_offline_banner !== undefined) {
            window.SHOW_OFFLINE_BANNER = !!s.show_offline_banner;
            // Re-evaluate banner visibility right now
            showOfflineBanner(_serverOffline, _networkState);
        }
        if (s.unlock_pin !== undefined) {
            const newPin = String(s.unlock_pin || '');
            if (newPin !== UNLOCK_PIN) {
                UNLOCK_PIN = newPin;
                // Resetting the PIN re-locks the kiosk immediately so a
                // forgotten unlock window doesn't outlive a PIN change.
                _unlockedUntil     = 0;
                _pinFailCount      = 0;
                _pinLockedOutUntil = 0;
                if (_pinKeypadOpen) closePinKeypad();
            }
        }
        if (s.auto_update_client !== undefined) {
            window.AISIGNX_AUTO_UPDATE_CLIENT = !!s.auto_update_client;
            notifyClientAutoUpdateFlag(window.AISIGNX_AUTO_UPDATE_CLIENT);
        }
        if (s.sync_playback_opt_out !== undefined) {
            const nextOptOut = !!s.sync_playback_opt_out;
            if (nextOptOut !== SYNC_PLAYBACK_OPT_OUT) {
                SYNC_PLAYBACK_OPT_OUT = nextOptOut;
                // Sync opt-out changes whether the playlist response includes
                // a sync block. Reload the playlist immediately so manual
                // controls do not fight an old in-memory wall-clock anchor.
                fetchPlaylist();
            }
        }
        applyInputLock();
        // Update media buttons visibility state
        if (mediaBtns) {
            if (!SHOW_MEDIA_BUTTONS) {
                mediaBtns.classList.remove('visible');
                document.body.style.cursor = isLocked() ? 'none' : '';
            } else {
                document.body.style.cursor = 'default';
            }
        }
        // Re-lock any active iframes
        if (activeSlide) {
            activeSlide.querySelectorAll('iframe').forEach(f => {
                f.tabIndex = isLocked() ? -1 : 0;
            });
        }
    }

    /* -----------------------------------------------------------------------
     * DOM
     * --------------------------------------------------------------------- */
    const playerEl   = document.getElementById('player');
    const overlayEl  = document.getElementById('status-overlay');
    const statusText = document.getElementById('status-text');
    const mediaBtns  = document.getElementById('media-buttons');
    const btnPrev    = document.getElementById('btn-prev');
    const btnRewind  = document.getElementById('btn-rewind');
    const btnPause   = document.getElementById('btn-pause');
    const btnFfwd    = document.getElementById('btn-ffwd');
    const btnNext    = document.getElementById('btn-next');
    const btnTrap    = document.getElementById('media-btn-trap');

    // Module-level so showSlide() can call it on every transition
    const MEDIA_BTN_IDLE_MS = 4000;   // hide after 4 s of no mouse movement
    let _mediaHideTimer = null;
    const VIDEO_STARTUP_TIMEOUT_MS = 8000;
    // Mid-playback stall watchdog: if a playing video stops making progress
    // (network/server dropped, partial cache, decode wedge) for this long, we
    // give up on it and advance instead of freezing the playlist. Without this
    // a video whose `ended` event never fires (because it stalled) would hold
    // the slide for its full duration cap (up to the 24h ceiling) -> "playing
    // too long" / long gaps between media when offline.
    const VIDEO_STALL_TIMEOUT_MS = 6000;
    // Native shells (Android WebView) buffer more aggressively — use a longer
    // stall window so normal mid-playback buffering is not mistaken for failure.
    const VIDEO_STALL_TIMEOUT_NATIVE_MS = 18_000;
    const PING_FAILS_BEFORE_OFFLINE = 3;
    const SSE_OFFLINE_GRACE_MS = 12_000;
    let _pingFailStreak = 0;
    let _sseOfflineTimer = null;

    function isNativeShell() {
        return (typeof window.AISignXNative !== 'undefined') ||
            (typeof window.signage === 'object' &&
             typeof window.signage.runCommand === 'function');
    }

    function resumePlayback() {
        try {
            document.querySelectorAll('video').forEach(v => {
                if (v.paused) v.play().catch(() => {});
            });
        } catch (_) {}
    }

    function showMediaButtons() {
        if (!SHOW_MEDIA_BUTTONS || !mediaBtns) return;
        clearTimeout(_mediaHideTimer);
        mediaBtns.classList.add('visible');
        _mediaHideTimer = setTimeout(hideMediaButtons, MEDIA_BTN_IDLE_MS);
    }

    function hideMediaButtons() {
        clearTimeout(_mediaHideTimer);
        if (mediaBtns) mediaBtns.classList.remove('visible');
    }

    // Apply initial input lock state
    applyInputLock();
    notifyClientAutoUpdateFlag(!!window.AISIGNX_AUTO_UPDATE_CLIENT);

    /* -----------------------------------------------------------------------
     * Playlist cache — localStorage so the display survives server restarts
     * --------------------------------------------------------------------- */
    function savePlaylistCache(data) {
        try {
            localStorage.setItem(CACHE_KEY, JSON.stringify({
                ts: Date.now(),
                data,
                // Persisted so cold boot / server-offline wall sync still tracks
                // server time (last known RTT midpoint). Refreshed on every save
                // and after each successful /server_time calibration.
                clockOffsetMs: serverClockOffsetMs,
            }));
        } catch (e) {
            // Storage quota exceeded or private mode — ignore
        }
    }

    function loadPlaylistCache() {
        try {
            const raw = localStorage.getItem(CACHE_KEY);
            if (!raw) return null;
            const row = JSON.parse(raw);
            if (row && typeof row.clockOffsetMs === 'number' && Number.isFinite(row.clockOffsetMs)) {
                serverClockOffsetMs = row.clockOffsetMs;
            }
            const data = row.data || null;
            if (data && playlistHasExpiredUrls(data)) return null;
            return data;
        } catch {
            return null;
        }
    }

    /** True when a signed /uploads/ URL is past its ``e=`` expiry. */
    function signedUrlExpired(url) {
        try {
            const u = new URL(url, window.location.origin);
            const e = parseInt(u.searchParams.get('e'), 10);
            if (!Number.isFinite(e)) return false;
            return (Date.now() / 1000) > (e - 30);
        } catch (_) {
            return false;
        }
    }

    function playlistHasExpiredUrls(data) {
        return (data.items || []).some((it) =>
            (it.type === 'image' || it.type === 'video') &&
            it.content_url && signedUrlExpired(it.content_url));
    }

    /** Merge fresh signed URLs from the server without changing the slide index. */
    function mergeFreshPlaylistUrls(pl) {
        if (!pl || !pl.items || !items.length) return false;
        let changed = false;
        const byId = new Map(pl.items.map((it) => [it.id, it]));
        for (let i = 0; i < items.length; i++) {
            const fresh = byId.get(items[i].id);
            if (fresh && fresh.content_url && fresh.content_url !== items[i].content_url) {
                items[i].content_url = fresh.content_url;
                changed = true;
            }
        }
        if (changed && playlist) {
            playlist.items = items;
            savePlaylistCache(playlist);
        }
        return changed;
    }

    function refreshActiveSlideMedia() {
        const item = items[currentIdx];
        if (!item || !activeSlide) return;
        if (item.type === 'image') {
            const img = activeSlide.querySelector('img');
            if (img && item.content_url) {
                img.dataset.retry = '';
                img.dataset.urlRefresh = '';
                img.removeAttribute('src');
                img.src = item.content_url;
            }
        } else if (item.type === 'video') {
            const vid = activeSlide.querySelector('video');
            if (vid && item.content_url) {
                vid.removeAttribute('src');
                vid.src = item.content_url;
                vid.load();
                if (!paused) vid.play().catch(() => {});
            }
        }
    }

    async function refreshPlaylistUrls() {
        try {
            const qs = clientQuery();
            const r = await fetch(`/api/display/${TOKEN}/playlist${qs ? '?' + qs : ''}`);
            if (!r.ok) return false;
            const json = await r.json();
            if (json.status !== 'success' || !json.playlist) return false;
            if (json.playlist.version && json.playlist.version !== lastPlaylistVersion) {
                if (json.playlist.sync && json.playlist.sync.enabled) {
                    await calibrateServerClock(3);
                }
                applyPlaylist(json.playlist);
                return true;
            }
            const changed = mergeFreshPlaylistUrls(json.playlist);
            if (changed) refreshActiveSlideMedia();
            return changed;
        } catch (e) {
            console.warn('[player] refreshPlaylistUrls failed:', e);
            return false;
        }
    }

    function absoluteUrl(url) {
        try {
            return new URL(url, window.location.origin).href;
        } catch (_) {
            return url;
        }
    }

    function collectPrefetchUrls(data) {
        const mediaUrls = (data.items || [])
            .filter(it => (it.type === 'image' || it.type === 'video') && it.content_url)
            .map(it => absoluteUrl(it.content_url));
        const pluginUrls = (data.items || [])
            .filter(it => (it.type === 'webpage' || it.plugin) && it.content_url
                          && /^\/plugin\//.test(it.content_url))
            .map(it => absoluteUrl(it.content_url));
        const current = (data.items || [])[currentIdx];
        if (current && current.content_url &&
            (current.type === 'image' || current.type === 'video')) {
            const currentUrl = absoluteUrl(current.content_url);
            const rest = mediaUrls.filter(u => u !== currentUrl);
            return {
                mediaUrls: [currentUrl, ...rest],
                pluginUrls,
            };
        }
        return { mediaUrls, pluginUrls };
    }

    function deliverPrefetchPayload(payload) {
        try {
            if (window.AISignXNative && window.AISignXNative.prefetchPlaylist) {
                window.AISignXNative.prefetchPlaylist(
                    JSON.stringify(payload.mediaUrls || []),
                    JSON.stringify(payload.pluginUrls || []),
                    payload.pagePath || window.location.pathname
                );
            }
        } catch (_) {}

        const sw = navigator.serviceWorker && navigator.serviceWorker.controller;
        if (!sw) return;
        if (payload.mediaUrls && payload.mediaUrls.length) {
            sw.postMessage({ type: 'prefetch_media', urls: payload.mediaUrls });
        }
        if (payload.pluginUrls && payload.pluginUrls.length) {
            sw.postMessage({ type: 'prefetch_plugins', urls: payload.pluginUrls });
        }
        sw.postMessage({
            type: 'prefetch_page',
            urls: [payload.pagePath || window.location.pathname],
        });
    }

    function warmPlaylistCaches(data, fromCache = false, urgent = false) {
        if (!data || !data.items || !data.items.length) return;
        const payload = collectPrefetchUrls(data);
        payload.pagePath = window.location.pathname;

        if (window._prefetchTimer) clearTimeout(window._prefetchTimer);
        // Synced walls: warm media sooner so every panel is more likely to
        // have bytes on disk before a network drop.
        const delay = (fromCache || urgent) ? 0
            : (data.sync && data.sync.enabled ? 2_000 : 5_000);
        window._prefetchTimer = setTimeout(() => {
            window._prefetchTimer = null;
            deliverPrefetchPayload(payload);
        }, delay);

        if (navigator.serviceWorker) {
            navigator.serviceWorker.ready
                .then(() => deliverPrefetchPayload(payload))
                .catch(() => {});
        } else {
            deliverPrefetchPayload(payload);
        }
    }

    // Offline banner — shown when playing from cache (if enabled in display settings).
    // The text/icon adapts to the current network state.
    const offlineBanner    = document.getElementById('offline-banner');
    const offlineBannerTxt = document.getElementById('offline-banner-text');
    const offlineBannerIco = document.getElementById('offline-banner-icon');

    function showOfflineBanner(show, state) {
        if (!offlineBanner) return;
        // Honor the per-display setting — hide entirely if user disabled it
        if (!window.SHOW_OFFLINE_BANNER) {
            offlineBanner.style.display = 'none';
            return;
        }
        if (!show) {
            offlineBanner.style.display = 'none';
            offlineBanner.dataset.state = '';
            return;
        }
        offlineBanner.style.display = 'flex';
        offlineBanner.dataset.state = state || 'server-offline';
        if (state === 'network-offline') {
            offlineBanner.style.background = 'rgba(160,30,30,0.88)';
            if (offlineBannerIco) offlineBannerIco.innerHTML = '&#128246;';   // antenna
            if (offlineBannerTxt) offlineBannerTxt.textContent =
                'Network offline — playing from cached playlist';
        } else {
            offlineBanner.style.background = 'rgba(180,100,0,0.85)';
            if (offlineBannerIco) offlineBannerIco.innerHTML = '&#9888;';      // warning
            if (offlineBannerTxt) offlineBannerTxt.textContent =
                'Server offline — playing from cached playlist';
        }
    }

    function showOverlay(msg, spin = true) {
        overlayEl.classList.remove('hidden');
        statusText.textContent = msg;
        const spinner = overlayEl.querySelector('.spinner');
        if (spinner) spinner.style.display = spin ? '' : 'none';
        // Show display name sub-label only when not spinning
        const lbl = document.getElementById('status-label');
        if (lbl) lbl.style.display = spin ? 'none' : '';
    }

    function hideOverlay() {
        overlayEl.classList.add('hidden');
    }

    /* -----------------------------------------------------------------------
     * Playlist state
     * --------------------------------------------------------------------- */
    let playlist     = null;   // current playlist object
    let items        = [];     // flat item array
    let currentIdx   = 0;
    let lastPlaylistVersion = null;
    let slideTimer   = null;
    let activeSlide  = null;
    let pingTimer    = null;
    let sseRetryDelay = SSE_BASE;
    let sseSource     = null;
    let blocked       = false;
    let paused        = false;

    /* -----------------------------------------------------------------------
     * Synchronized playback state (Phase 4)
     *
     * When the playlist response includes a `sync` block the display
     * derives its current slide index and the time until the next slide
     * from a shared wall-clock anchor. All displays in the same group
     * compute the same answer, so they stay in lockstep without the
     * server having to broadcast "show slide N now" messages.
     *
     * `serverClockOffsetMs` is `serverNow - localNow` measured via
     * `/api/display/<token>/server_time` (RTT midpoint) whenever a synced
     * playlist is applied from the network — not from the embedded
     * `sync.server_now_ms` snapshot, which can be seconds stale when HTML
     * was rendered before the browser ran JavaScript.
     * --------------------------------------------------------------- */
    let syncBlock          = null;   // last `sync` object received, or null
    let serverClockOffsetMs = 0;

    function localToServerNowMs()  { return Date.now() + serverClockOffsetMs; }

    /**
     * Given the current sync block, return {idx, msUntilNext}. Returns
     * null if sync isn't active, in which case the caller falls back to
     * the per-item duration timer.
     */
    function syncedTarget() {
        if (!syncBlock || !syncBlock.enabled || !items.length) return null;
        const total = syncBlock.cycle_total_ms;
        const table = syncBlock.item_durations_ms || [];
        if (!total || total <= 0 || !table.length) return null;
        const nowMs = localToServerNowMs();
        const anchor = syncBlock.anchor_unix_ms;
        if (nowMs < anchor) {
            return { idx: 0, msUntilNext: anchor - nowMs };
        }
        const elapsed = ((nowMs - anchor) % total + total) % total;
        let acc = 0;
        for (let i = 0; i < table.length; i++) {
            const slotEnd = acc + table[i];
            if (elapsed < slotEnd) {
                return { idx: i, msUntilNext: slotEnd - elapsed };
            }
            acc = slotEnd;
        }
        // Fallback (shouldn't hit -- elapsed is reduced mod total).
        return { idx: 0, msUntilNext: table[0] };
    }

    /** True when wall-clock sync says the cycle is currently in items[slideIdx]. */
    function syncedWallTimedForSlideIndex(slideIdx) {
        const t = syncedTarget();
        return (t && t.idx === slideIdx) ? t : null;
    }

    /**
     * Align <video> currentTime to the correct point inside this slide's
     * wall-clock slot (sync groups). No-ops when not in sync or wrong slide.
     */
    function syncVideoWallClock(vid, item, slideIdx, syncTgt) {
        if (!vid || !item || !syncBlock || !syncTgt) return;
        const table = syncBlock.item_durations_ms || [];
        const slotMs = table[slideIdx];
        if (!slotMs || slotMs <= 0) return;
        const elapsedInSlotMs = Math.max(0, slotMs - syncTgt.msUntilNext);
        const clipStart = (item.clip_start != null && item.clip_start > 0) ? item.clip_start : 0;
        const clipEnd = (item.clip_end != null && item.clip_end > 0) ? item.clip_end : null;
        const dur = vid.duration;
        let maxT = (dur && isFinite(dur) && dur > 0) ? dur - 0.05 : clipStart + elapsedInSlotMs / 1000;
        if (clipEnd != null && clipEnd > 0) maxT = Math.min(maxT, clipEnd - 0.05);
        let want = clipStart + elapsedInSlotMs / 1000;
        if (want < clipStart) want = clipStart;
        if (want > maxT) want = Math.max(clipStart, maxT);
        if (Math.abs(vid.currentTime - want) > 0.35) {
            try { vid.currentTime = want; } catch (_) {}
        }
    }

    let _syncClockIv = null;       // periodic /server_time recalibration
    let _syncDriftIv = null;       // fast drift watchdog (sub-second granularity)

    function stopSyncClockMaintenance() {
        if (_syncClockIv) {
            clearInterval(_syncClockIv);
            _syncClockIv = null;
        }
        if (_syncDriftIv) {
            clearInterval(_syncDriftIv);
            _syncDriftIv = null;
        }
    }

    /** Re-read wall clock vs timers after calibration or clock drift. */
    function rescheduleSyncedPlaybackFromWallClock() {
        if (paused || !syncBlock || !syncBlock.enabled) return;
        const tgt = syncedTarget();
        if (!tgt) return;
        if (tgt.idx !== currentIdx) {
            clearTimeout(slideTimer);
            console.log(`[sync] drift recovery: ${currentIdx} -> ${tgt.idx}`);
            showSlide(tgt.idx);
            return;
        }
        clearTimeout(slideTimer);
        slideTimer = setTimeout(advance, Math.max(50, tgt.msUntilNext));
        const item = items[currentIdx];
        if (item && item.type === 'video') {
            const vid = activeVideo();
            if (vid) {
                const t2 = syncedTarget();
                if (t2 && t2.idx === currentIdx) syncVideoWallClock(vid, item, currentIdx, t2);
            }
        }
    }

    /**
     * Drift watchdog. Runs every 1.5s.
     *
     * Three jobs:
     *   1. Detect wake-from-suspend / long timer gaps (laptop lid closed,
     *      screen-saver, hidden browser tab). When the gap between ticks
     *      is much larger than expected we force a fresh clock calibration
     *      because the OS clock may have jumped, especially on Linux/Mac.
     *   2. When sync is enabled, snap currentIdx to the wall-clock target
     *      if they disagree -- the user's request: "if a client gets out
     *      of sync it should resync by starting off at the same time on
     *      the next media item and skip whatever it's at."
     *   3. Re-align <video> currentTime inside a slot if drift > 1s.
     */
    const WATCHDOG_MS = 1500;
    const SUSPEND_GAP_MS = 5_000;         // gap > this => probable suspend/throttle
    let _lastWatchdogTick = 0;
    function tickSyncDriftWatchdog() {
        const now = Date.now();
        if (_lastWatchdogTick) {
            const gap = now - _lastWatchdogTick;
            if (gap > SUSPEND_GAP_MS) {
                console.log(`[sync] watchdog gap ${(gap/1000).toFixed(1)}s -- forcing 3-sample recalibration`);
                calibrateServerClock(3);
            }
        }
        _lastWatchdogTick = now;

        if (paused || blocked) return;
        if (!syncBlock || !syncBlock.enabled || !items.length) return;
        const tgt = syncedTarget();
        if (!tgt) return;
        if (tgt.idx !== currentIdx) {
            console.log(`[sync] watchdog drift: ${currentIdx} -> ${tgt.idx}, snapping forward`);
            diagLog('sync', 'watchdog', 'slide drift', { from: currentIdx, to: tgt.idx });
            clearTimeout(slideTimer);
            showSlide(tgt.idx);
            return;
        }
        const item = items[currentIdx];
        if (item && item.type === 'video') {
            const vid = activeVideo();
            if (!vid || !isFinite(vid.duration) || vid.duration <= 0) return;
            const table = syncBlock.item_durations_ms || [];
            const slotMs = table[currentIdx];
            if (!slotMs) return;
            const elapsedMs = Math.max(0, slotMs - tgt.msUntilNext);
            const clipStart = (item.clip_start != null && item.clip_start > 0) ? item.clip_start : 0;
            const want = clipStart + elapsedMs / 1000;
            if (Math.abs(vid.currentTime - want) > 1.0) {
                console.log(`[sync] video drift ${(vid.currentTime - want).toFixed(2)}s -> reseek`);
                syncVideoWallClock(vid, item, currentIdx, tgt);
            }
        }
    }

    function startSyncClockMaintenance() {
        // 60s clock recalibration is global (clock plugin needs it whether
        // or not the group syncs playback) and lives in startClockRecalibration().
        if (!_syncClockIv) {
            _syncClockIv = setInterval(async () => {
                if (!syncBlock || !syncBlock.enabled) {
                    stopSyncClockMaintenance();
                    return;
                }
                // Stronger 3-sample recalibration every 6 minutes for sync
                // groups so the per-slide watchdog has a high-confidence
                // target even after long uptime.
                await calibrateServerClock(3);
            }, 6 * 60 * 1000);
        }
        if (!_syncDriftIv) {
            _syncDriftIv = setInterval(tickSyncDriftWatchdog, WATCHDOG_MS);
        }
    }

    /**
     * Calibrate the local-to-server clock offset.
     *
     * `samples` round-trips are issued back-to-back; we keep the offset
     * from the lowest-RTT sample (NTP-style "use the round-trip with the
     * least jitter"). A single round-trip is enough for slide-cadence
     * sync, but the clock plugin renders every second and benefits a lot
     * from rejecting outliers: a single bad RTT (e.g. 600ms due to GC or
     * Wi-Fi retransmit) shifts the estimate by ~300ms, which is the kind
     * of fleet-wide skew we'd previously see.
     *
     * Returns true on success, false if every sample failed.
     */
    async function calibrateServerClock(samples) {
        if (typeof samples !== 'number' || samples < 1) samples = 1;
        let best = null;                                 // {rtt, offset}
        for (let i = 0; i < samples; i++) {
            try {
                const t0 = Date.now();
                const r  = await fetch(`/api/display/${TOKEN}/server_time`,
                                       { cache: 'no-store' });
                const t1 = Date.now();
                if (!r.ok) continue;
                const j = await r.json();
                if (j.status !== 'success' || typeof j.server_now_ms !== 'number') continue;
                const rtt    = t1 - t0;
                const offset = j.server_now_ms - (t0 + t1) / 2;
                if (!best || rtt < best.rtt) best = { rtt, offset };
                // If we got an unrealistically fast sample (<= 25ms RTT)
                // there's no point continuing -- it's already as good as
                // it gets on this network.
                if (rtt <= 25) break;
            } catch (e) {
                // try next sample
            }
        }
        if (!best) {
            console.warn('[sync] server_time calibration failed (all samples)');
            return false;
        }
        const prev = serverClockOffsetMs;
        serverClockOffsetMs = best.offset;
        console.log(`[sync] clock offset=${best.offset.toFixed(0)}ms rtt=${best.rtt}ms (Δ=${(best.offset - prev).toFixed(0)}ms, samples=${samples})`);
        diagLog('sync', 'calibrate', 'clock offset updated', {
            offset_ms: Math.round(best.offset),
            rtt_ms:    best.rtt,
            delta_ms:  Math.round(best.offset - prev),
            samples:   samples,
        });
        try {
            if (playlist && playlist.items && playlist.items.length) {
                savePlaylistCache(playlist);
            }
        } catch (_) {}
        rescheduleSyncedPlaybackFromWallClock();
        broadcastClockOffsetToFrames();
        return true;
    }

    /**
     * Background clock recalibration. Runs every 60s regardless of whether
     * group-sync is on, so the clock plugin (and any plugin using
     * `signageDate()` / `signageNowMs()`) stays accurate across a fleet of
     * different hardware whose OS clocks drift at different rates. When
     * group-sync IS on, this also keeps `syncedTarget()` honest so the
     * fast drift watchdog can snap forward without chasing a wrong target.
     */
    const CLOCK_RECAL_MS = 60 * 1000;
    let _clockRecalIv = null;
    function startClockRecalibration() {
        if (_clockRecalIv) return;
        _clockRecalIv = setInterval(() => {
            if (_serverOffline) return;        // no point hammering an unreachable server
            calibrateServerClock(1);
        }, CLOCK_RECAL_MS);
    }

    /** Detect this player's capabilities (codecs, screen size, audio)
     *  and POST them to the server. Lets the server filter playlist
     *  items the player can't render. Failure is non-fatal -- the
     *  server just keeps the previous snapshot (or "unknown" => send
     *  everything). */
    async function reportCapabilities() {
        try {
            const v = document.createElement('video');
            const codecs = [];
            // Probe a tiny set; expand if real-world devices need more.
            // canPlayType returns "" / "maybe" / "probably" -- treat
            // anything non-empty as "yes, we can".
            const probes = [
                ['h264', 'video/mp4; codecs="avc1.42E01E"'],
                ['vp9',  'video/webm; codecs="vp9"'],
                ['av1',  'video/mp4; codecs="av01.0.05M.08"'],
                ['hevc', 'video/mp4; codecs="hev1.1.6.L93.B0"'],
            ];
            for (const [name, mime] of probes) {
                if (v.canPlayType(mime)) codecs.push(name);
            }
            const caps = {
                screen_w: window.screen.width,
                screen_h: window.screen.height,
                max_video_height: window.screen.height,
                max_image_dim: Math.max(window.screen.width, window.screen.height) * 2,
                codecs,
                audio: true,        // browsers always have audio capability
                browser: navigator.userAgent.split(' ').slice(-2).join(' '),
            };
            const r = await fetch(`/api/display/${TOKEN}/capabilities`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(caps),
                cache: 'no-store',
            });
            if (r.ok) {
                const j = await r.json();
                console.log(`[caps] reported ${codecs.length} codec(s); server changed=${j.changed}`);
            }
        } catch (e) {
            console.warn('[caps] capability report failed:', e);
        }
    }

    /* -----------------------------------------------------------------------
     * Helper — returns the <video> element of the active slide, or null
     * --------------------------------------------------------------------- */
    function activeVideo() {
        return activeSlide ? activeSlide.querySelector('video') : null;
    }

    /** Stop audio/video in a slide before it leaves the screen. */
    function retireSlide(slide) {
        if (!slide) return;
        if (typeof slide._stopVideoStallWatch === 'function') slide._stopVideoStallWatch();
        if (slide._videoPendingCapTimer) {
            clearTimeout(slide._videoPendingCapTimer);
            slide._videoPendingCapTimer = null;
        }
        slide.querySelectorAll('video, audio').forEach(el => {
            try {
                el.pause();
                el.muted = true;
                el.volume = 0;
                el.removeAttribute('src');
                el.load();
            } catch (_) {}
        });
        slide.querySelectorAll('iframe').forEach(frame => {
            try {
                if (frame.contentWindow) {
                    frame.contentWindow.postMessage({ type: 'signage:pause' }, '*');
                    frame.contentWindow.postMessage({ type: 'signage:stop' }, '*');
                }
            } catch (_) {}
            try { frame.src = 'about:blank'; } catch (_) {}
        });
    }

    function clearPreloadedSlide() {
        if (_preloadedSlide) retireSlide(_preloadedSlide);
        _preloadedSlide = null;
        _preloadedIdx   = -1;
    }

    /* -----------------------------------------------------------------------
     * Slide rendering
     * --------------------------------------------------------------------- */
    function modeClass(mode) {
        const safe = (mode || 'fit').toLowerCase();
        return `mode-${['fit','fill','stretch','center'].includes(safe) ? safe : 'fit'}`;
    }

    function buildSlide(item, slideIdx = null) {
        const div = document.createElement('div');
        div.className = `slide ${modeClass(item.aspect_mode)}`;

        if (item.type === 'image') {
            const img = document.createElement('img');
            img.src = item.content_url;
            img.alt = '';
            img.draggable = false;
            // If the image fails to load (e.g. offline + not cached), skip this slide
            img.addEventListener('error', () => {
                const idx = (slideIdx != null) ? slideIdx : currentIdx;
                const failSlide = () => {
                    console.warn('[player] Image failed to load, advancing:',
                        (items[idx] && items[idx].content_url) || item.content_url);
                    if (activeSlide === div) setTimeout(advance, 500);
                };
                if (!img.dataset.urlRefresh) {
                    img.dataset.urlRefresh = '1';
                    refreshPlaylistUrls().then((ok) => {
                        const it = items[idx];
                        if (ok && it && it.content_url) {
                            img.dataset.retry = '';
                            img.removeAttribute('src');
                            img.src = it.content_url;
                            return;
                        }
                        if (!img.dataset.retry) {
                            img.dataset.retry = '1';
                            const src = it && it.content_url ? it.content_url : item.content_url;
                            img.removeAttribute('src');
                            img.src = src;
                            return;
                        }
                        failSlide();
                    });
                    return;
                }
                if (!img.dataset.retry) {
                    img.dataset.retry = '1';
                    const src = items[idx] && items[idx].content_url
                        ? items[idx].content_url : item.content_url;
                    img.removeAttribute('src');
                    img.src = src;
                    return;
                }
                failSlide();
            });
            div.appendChild(img);

        } else if (item.type === 'video') {
            const vid = document.createElement('video');
            let videoStartupTimer = null;
            const clearVideoStartupTimer = () => {
                if (videoStartupTimer) {
                    clearTimeout(videoStartupTimer);
                    videoStartupTimer = null;
                }
            };
            const failVideo = (reason) => {
                clearVideoStartupTimer();
                if (div._stopVideoStallWatch) div._stopVideoStallWatch();
                console.warn('[player] Video failed/stalled, advancing:', reason, item.content_url);
                diagLog('player', 'video', 'video failed or stalled', {
                    reason,
                    url: item.content_url,
                    ready_state: vid.readyState,
                    network_state: vid.networkState,
                });
                if (activeSlide === div) setTimeout(advance, 250);
            };
            vid.controls = false;
            vid.setAttribute('controlsList', 'nodownload noplaybackrate nofullscreen');
            vid.setAttribute('disablepictureinpicture', '');
            vid.setAttribute('playsinline', '');
            vid.setAttribute('webkit-playsinline', '');
            vid.tabIndex = -1;
            vid.addEventListener('keydown', (e) => {
                if (!isLocked() && !_pinKeypadOpen) return;
                e.preventDefault();
                e.stopPropagation();
            }, true);
            vid.poster = BLACK_VIDEO_POSTER;
            vid.style.backgroundColor = '#000';
            vid.autoplay = true;
            vid.playsInline = true;
            vid.loop = false;
            vid.preload = 'auto';
            vid.src = item.content_url;
            div.classList.add('video-pending');
            const markVideoReady = () => div.classList.remove('video-pending');
            vid.addEventListener('loadeddata', () => {
                clearVideoStartupTimer();
                if (typeof vid.requestVideoFrameCallback === 'function') {
                    vid.requestVideoFrameCallback(markVideoReady);
                }
            }, { once: true });
            vid.addEventListener('loadedmetadata', clearVideoStartupTimer, { once: true });
            vid.addEventListener('canplay', clearVideoStartupTimer, { once: true });
            vid.addEventListener('timeupdate', () => {
                clearVideoStartupTimer();
                markVideoReady();
            }, { once: true });
            vid.addEventListener('playing', () => {
                clearVideoStartupTimer();
                markVideoReady();
            }, { once: true });
            // Audio: server-resolved item.audio_enabled (or fallback to !mute_audio)
            // gated by the display master VOLUME. Browsers also need the muted
            // attribute set when there is no audio so autoplay isn't blocked.
            vid._audioWanted = (item.audio_enabled !== undefined)
                ? !!item.audio_enabled
                : !item.mute_audio;
            applyVideoAudio(vid);

            // If the video fails to load (e.g. offline + not cached), skip this slide
            vid.addEventListener('error', () => {
                if (!vid.dataset.urlRefresh) {
                    vid.dataset.urlRefresh = '1';
                    refreshPlaylistUrls().then((ok) => {
                        const it = items[(slideIdx != null) ? slideIdx : currentIdx];
                        if (ok && it && it.content_url && activeSlide === div) {
                            vid.src = it.content_url;
                            vid.load();
                            if (!paused) vid.play().catch(() => {});
                            return;
                        }
                        failVideo('error event');
                    });
                    return;
                }
                failVideo('error event');
            });
            div._startVideoStartupTimer = () => {
                clearVideoStartupTimer();
                if (vid.readyState >= 2) return;
                videoStartupTimer = setTimeout(() => {
                    if (activeSlide === div && vid.readyState < 2) {
                        failVideo('startup timeout');
                    }
                }, VIDEO_STARTUP_TIMEOUT_MS);
            };

            // Mid-playback stall watchdog. We track the last time currentTime
            // actually moved; if it stops advancing while the slide is active
            // and the video is neither paused nor ended, we advance. This is
            // what protects against a video that started fine but then wedged
            // because the network/server dropped mid-stream (common offline).
            let stallTimer    = null;
            let lastMediaTime = 0;
            let lastProgressAt = Date.now();
            const markProgress = () => {
                lastMediaTime  = vid.currentTime || 0;
                lastProgressAt = Date.now();
            };
            vid.addEventListener('timeupdate', markProgress);
            vid.addEventListener('playing', markProgress);
            const stallTimeoutMs = isNativeShell()
                ? VIDEO_STALL_TIMEOUT_NATIVE_MS
                : VIDEO_STALL_TIMEOUT_MS;
            div._startVideoStallWatch = () => {
                if (stallTimer) clearInterval(stallTimer);
                lastProgressAt = Date.now();
                stallTimer = setInterval(() => {
                    // Self-terminate once this slide is no longer on screen.
                    if (activeSlide !== div || vid.isConnected === false) {
                        clearInterval(stallTimer);
                        stallTimer = null;
                        return;
                    }
                    if (paused || vid.paused || vid.ended) {
                        lastProgressAt = Date.now();
                        return;
                    }
                    // Still fetching bytes — not a stall.
                    if (vid.networkState === 2 || vid.readyState < 2) {
                        lastProgressAt = Date.now();
                        return;
                    }
                    if (Math.abs((vid.currentTime || 0) - lastMediaTime) > 0.05) {
                        markProgress();
                        return;
                    }
                    if (Date.now() - lastProgressAt > stallTimeoutMs) {
                        clearInterval(stallTimer);
                        stallTimer = null;
                        failVideo('playback stall');
                    }
                }, 1000);
            };
            // Safety: never leave the black video-pending overlay up forever.
            div._videoPendingCapTimer = setTimeout(() => {
                if (activeSlide === div && div.classList.contains('video-pending')) {
                    markVideoReady();
                }
            }, 20_000);
            vid.addEventListener('ended', () => {
                if (stallTimer) { clearInterval(stallTimer); stallTimer = null; }
            });
            div._stopVideoStallWatch = () => {
                if (stallTimer) { clearInterval(stallTimer); stallTimer = null; }
            };

            // Clip support — seek to start point once metadata is ready
            const clipStart = (item.clip_start != null && item.clip_start > 0) ? item.clip_start : 0;
            const clipEnd   = (item.clip_end   != null && item.clip_end   > 0) ? item.clip_end   : null;

            const wall = (slideIdx != null) ? syncedWallTimedForSlideIndex(slideIdx) : null;
            if (wall) {
                const doWall = () => {
                    const t = syncedTarget();
                    if (t && t.idx === slideIdx) syncVideoWallClock(vid, item, slideIdx, t);
                };
                if (vid.readyState >= 1) doWall();
                else vid.addEventListener('loadedmetadata', doWall, { once: true });
            } else if (clipStart > 0) {
                vid.addEventListener('loadedmetadata', () => {
                    vid.currentTime = clipStart;
                }, { once: true });
            }

            if (clipEnd != null) {
                // Poll timeupdate to advance when we hit clip_end.
                // Synced groups: leave the wall-clock timer in charge.
                vid.addEventListener('timeupdate', function onTime() {
                    if (vid.currentTime >= clipEnd) {
                        vid.removeEventListener('timeupdate', onTime);
                        if (syncedTarget()) return;
                        advance();
                    }
                });
            } else {
                // No clip end — advance when video ends naturally.
                // Synced groups: ignore (the wall-clock timer in showSlide
                // controls advancement); for unsynced this is the normal path.
                vid.addEventListener('ended', () => {
                    if (syncedTarget()) return;
                    advance();
                }, { once: true });
            }

            div.appendChild(vid);

        } else if (item.type === 'webpage') {
            const frame = document.createElement('iframe');
            frame.src = item.content_url;
            // Per-plugin sandbox + allow attributes when the server provided
            // them (Phase 3 plugin sandboxing). Falls back to the legacy
            // permissive set so non-plugin webpages and older API responses
            // continue to work.
            const pluginInfo = item.plugin || null;
            if (pluginInfo && typeof pluginInfo.sandbox === 'string' && pluginInfo.sandbox) {
                frame.sandbox = pluginInfo.sandbox;
                if (pluginInfo.allow) frame.allow = pluginInfo.allow;
            } else {
                frame.sandbox = 'allow-scripts allow-same-origin allow-forms allow-popups allow-presentation';
            }
            frame.allowFullscreen = true;
            // Tag plugin iframes so live policy-change events (Phase 4)
            // can find and reload the right frame.
            if (pluginInfo) {
                if (pluginInfo.key)  frame.dataset.pluginKey  = pluginInfo.key;
                if (pluginInfo.type) frame.dataset.pluginType = pluginInfo.type;
            } else if (item.plugin_type) {
                frame.dataset.pluginType = item.plugin_type;
            }

            // When input is locked, prevent the iframe from ever gaining keyboard focus.
            // Pointer events are already blocked by the #media-btn-trap overlay.
            if (isLocked()) {
                frame.tabIndex = -1;
                frame.addEventListener('focus', () => frame.blur());
            }

            // Distinguish *plugin* iframes (our own /plugin/<type> URL) from
            // arbitrary user-supplied webpages.
            const isPlugin = !!item.plugin || /^\/plugin\//.test(item.content_url || '');

            // Required for showSlide()'s activation hook -- no-op now that we
            // don't run a load timeout, but the hook still calls this.
            div._startFailTimer = function () {};

            // X-Frame-Options "refused to connect" overlay for non-plugin
            // webpages only. Plugins live behind the duration cap and never
            // show this banner -- if the SW returns 503, the iframe stays
            // blank for a moment and the playlist's normal duration timer
            // moves us on. That's far less disruptive than the previous
            // "Plugin unavailable" placeholder which produced false-skips.
            const errOverlay = document.createElement('div');
            errOverlay.style.cssText = [
                'display:none',
                'position:absolute',
                'inset:0',
                'background:#111',
                'color:#ccc',
                'font:16px/1.6 sans-serif',
                'align-items:center',
                'justify-content:center',
                'flex-direction:column',
                'gap:0.75rem',
                'text-align:center',
                'padding:2rem',
                'z-index:10',
            ].join(';');

            const domain = (() => { try { return new URL(frame.src, window.location.origin).hostname; } catch { return frame.src; } })();
            errOverlay.innerHTML = `
                <div style="font-size:3rem;">🚫</div>
                <div style="font-size:1.2rem;color:#fff;font-weight:600;">${domain} refused to connect</div>
                <div style="max-width:480px;color:#999;font-size:0.9rem;">
                    This site blocks being displayed inside another page
                    (<code style="color:#aaa;">X-Frame-Options</code> / <code style="color:#aaa;">frame-ancestors</code>).
                    Try using a screenshot or a different URL that allows embedding.
                </div>
                <div style="color:#666;font-size:0.8rem;word-break:break-all;">${frame.src}</div>`;

            // For non-plugin webpages: if the iframe loads with an empty body
            // (X-Frame-Options block), show the overlay and retry every 5s.
            // We do NOT do this check for plugin iframes -- plugins routinely
            // have brief empty-body windows during initial JS bootstrap that
            // would trigger false positives.
            if (!isPlugin) {
                frame.addEventListener('load', () => {
                    try {
                        const doc = frame.contentDocument;
                        if (doc && (!doc.body || doc.body.innerHTML.trim() === '')) {
                            errOverlay.style.display = 'flex';
                            if (!div._retryTimer) {
                                div._retryTimer = setTimeout(() => {
                                    div._retryTimer = null;
                                    frame.src = frame.src;
                                }, 5000);
                            }
                        } else {
                            errOverlay.style.display = 'none';
                            if (div._retryTimer) { clearTimeout(div._retryTimer); div._retryTimer = null; }
                        }
                    } catch (e) {
                        // SecurityError = cross-origin frame loaded successfully
                        errOverlay.style.display = 'none';
                        if (div._retryTimer) { clearTimeout(div._retryTimer); div._retryTimer = null; }
                    }
                });
            }

            div.appendChild(frame);
            div.appendChild(errOverlay);
        }

        return div;
    }

    /* -----------------------------------------------------------------------
     * Preloading — load the next slide's media while the current one plays
     * so there's no delay when it's time to switch.
     * We keep one preloaded slide element ready to swap in.
     * --------------------------------------------------------------------- */
    let _preloadedSlide  = null;
    let _preloadedIdx    = -1;

    function preloadNext() {
        if (!items.length) return;
        const nextIdx = (currentIdx + 1) % items.length;
        if (_preloadedIdx === nextIdx) return; // already preloaded

        const item = items[nextIdx];
        const slide = buildSlide(item, nextIdx);

        if (item.type === 'image') {
            const img = slide.querySelector('img');
            if (img) img.decode().catch(() => {}); // warm browser decode cache
        } else if (item.type === 'video') {
            const vid = slide.querySelector('video');
            if (vid) {
                vid.preload = 'auto';
                vid.muted = true;
                vid.autoplay = false;
                // Start loading but keep paused — autoplay fires when shown
                vid.load();
            }
        }
        // iframes: src is set in buildSlide but the frame is not in the DOM
        // so the browser won't load it yet — that's fine, the goal is to have
        // the element ready so insertion is instant.

        _preloadedSlide = slide;
        _preloadedIdx   = nextIdx;
    }

    function takePreloaded(idx) {
        if (_preloadedIdx === idx && _preloadedSlide) {
            const slide = _preloadedSlide;
            _preloadedSlide = null;
            _preloadedIdx   = -1;
            return slide;
        }
        return null;
    }

    // Build a tile-grid overlay used by the 'puzzle' transition. The
    // overlay covers the slide with opaque tiles; staggered per-tile
    // transition delays make them fall away in a randomised order so the
    // underlying image appears piece-by-piece.
    function buildPuzzleOverlay(slide) {
        const cols = 8, rows = 5;
        const overlay = document.createElement('div');
        overlay.className = 'puzzle-overlay';
        overlay.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
        overlay.style.gridTemplateRows    = `repeat(${rows}, 1fr)`;
        const order = [];
        for (let i = 0; i < cols * rows; i++) order.push(i);
        // Fisher-Yates shuffle so each run feels different.
        for (let i = order.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [order[i], order[j]] = [order[j], order[i]];
        }
        for (let i = 0; i < cols * rows; i++) {
            const tile = document.createElement('div');
            tile.className = 'tile';
            // Delay scales with shuffled position; total ~900ms reveal.
            tile.style.transitionDelay = (order[i] * 18) + 'ms';
            overlay.appendChild(tile);
        }
        slide.appendChild(overlay);
        return overlay;
    }

    function showSlide(idx) {
        if (!items.length) return;
        idx = ((idx % items.length) + items.length) % items.length;
        currentIdx = idx;

        // Proof-of-Play: close out the previous slide before swapping in the
        // new one. completed=true because we got here through the normal
        // advance() / 'ended' path; goBack/skip flows pass completed=false.
        proofOfPlayMark(true);

        const item = items[idx];
        // Use preloaded slide if available, otherwise build fresh
        const newSlide = takePreloaded(idx) || buildSlide(item, idx);
        // Per-item transition class. Server already resolved playlist
        // defaults; 'random' means the player picks per slide.
        // Keep this list in sync with templates/display_player.html CSS
        // and playlists.py / display_player.py validators.
        const KNOWN_TRANS = [
            'cut', 'fade', 'crossfade', 'wipe',
            'slide-left', 'slide-right', 'slide-up', 'slide-down',
            'zoom', 'spin', 'flip', 'iris', 'puzzle'
        ];
        let trans = (item.transition || 'cut').toLowerCase();
        if (trans === 'random') {
            // If the playlist defined a custom random pool (subset of
            // KNOWN_TRANS, validated server-side), prefer it. Otherwise
            // fall back to every animated transition the player knows.
            // 'cut' is excluded from the default pool – nobody picks
            // "random" expecting no transition.
            const customPool = (playlist && Array.isArray(playlist.random_pool))
                ? playlist.random_pool.filter(t => KNOWN_TRANS.includes(t) && t !== 'cut')
                : [];
            const pool = customPool.length
                ? customPool
                : KNOWN_TRANS.filter(t => t !== 'cut');
            trans = pool[Math.floor(Math.random() * pool.length)];
        }
        if (trans === 'none') trans = 'cut';
        if (!KNOWN_TRANS.includes(trans)) trans = 'cut';
        newSlide.classList.add('trans-' + trans);
        if (activeSlide) retireSlide(activeSlide);
        playerEl.appendChild(newSlide);

        // Puzzle: drop a tile grid in front of the new slide and fade tiles
        // out in a randomised order so the image is "assembled".
        let puzzleOverlay = null;
        if (trans === 'puzzle') {
            puzzleOverlay = buildPuzzleOverlay(newSlide);
        }

        // Trigger reflow so transition fires
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                // Fade out old slide
                if (activeSlide) {
                    const old = activeSlide;
                    // 'cut' has no animation; 'puzzle' uses an overlay on
                    // the new slide and the underlying slide swap should
                    // also be instant so the puzzle is the only motion.
                    if (trans === 'cut' || trans === 'puzzle') {
                        retireSlide(old);
                        old.remove();
                    } else {
                        old.classList.replace('active', 'exiting');
                        retireSlide(old);
                        old.addEventListener('transitionend', () => old.remove(), { once: true });
                        // Safety removal if transition never fires (longest
                        // transition is ~800ms; give it some headroom).
                        setTimeout(() => old.remove(), 1500);
                    }
                }
                newSlide.classList.add('active');
                activeSlide = newSlide;

                // Kick off the puzzle reveal now that the slide is on-screen.
                if (puzzleOverlay) {
                    requestAnimationFrame(() => {
                        puzzleOverlay.classList.add('gone');
                        setTimeout(() => puzzleOverlay.remove(), 1500);
                    });
                }

                // Start the iframe load-timeout clock now that this slide is
                // active. Built lazily so preloaded slides don't time out
                // while they're still in the wings.
                if (typeof newSlide._startFailTimer === 'function') {
                    newSlide._startFailTimer();
                }
                if (typeof newSlide._startVideoStartupTimer === 'function') {
                    newSlide._startVideoStartupTimer();
                }
                if (typeof newSlide._startVideoStallWatch === 'function') {
                    newSlide._startVideoStallWatch();
                }

                // Tell any plugin iframe in this slide about current online state
                notifyIframeOfState(newSlide);

                // Explicitly play video — autoplay on dynamically created elements
                // is unreliable in Electron/Chromium when not yet attached to DOM.
                if (item.type === 'video') {
                    const vid = newSlide.querySelector('video');
                    if (vid && !paused) {
                        vid.play().catch(() => {
                            // Autoplay blocked — advance to next item
                            advance();
                        });
                    }
                }

                // Start preloading the next slide immediately
                preloadNext();

                // Report what's now playing to the server immediately
                ping();

                // Update video-only buttons visibility — do NOT auto-show on slide change
                if (SHOW_MEDIA_BUTTONS && mediaBtns) {
                    mediaBtns.classList.toggle('has-video', item.type === 'video');
                }

                // If paused, immediately pause any new video and don't start the timer
                if (paused) {
                    const vid = activeVideo();
                    if (vid) { vid.pause(); }
                    return;
                }

                // For video — duration is driven by 'ended' event; use item.duration as max
                // For plugin iframes — driven by signage:complete postMessage; duration is safety fallback
                // For image/plain webpage — use item.duration
                //
                // SYNCED playback override: when a sync block is active we
                // ignore the 'ended' / 'signage:complete' early signals
                // (handled in advance()) and time the slide to expire
                // exactly when the wall-clock anchor says the next slot
                // should start. This is what keeps every display in the
                // group landing on the same slide together.
                const syncTgt = syncedTarget();
                if (syncTgt && syncTgt.idx === currentIdx) {
                    clearTimeout(slideTimer);
                    slideTimer = setTimeout(advance, Math.max(50, syncTgt.msUntilNext));
                } else if (item.type === 'video') {
                    // Duration cap for video:
                    // - If clip_end is set: cap = (clip_end - clip_start) + 1s buffer
                    // - If item.duration > 0: use as hard cap
                    // - Otherwise: 24h ceiling so 'ended'/'timeupdate' always fires first
                    let dur;
                    if (item.clip_end != null && item.clip_end > 0) {
                        const clipLen = item.clip_end - (item.clip_start || 0);
                        dur = (clipLen + 1) * 1_000;
                    } else if (item.duration > 0) {
                        dur = item.duration * 1_000;
                    } else {
                        dur = 86_400_000; // 24h ceiling
                    }
                    clearTimeout(slideTimer);
                    slideTimer = setTimeout(advance, dur);
                } else if (item.plugin) {
                    // Plugins normally signal completion themselves via the
                    // signage:complete postMessage, which fires advance() the
                    // moment the plugin's own duration elapses. The timer
                    // here is a SAFETY CAP for the case where the postMessage
                    // never arrives -- iframe was reloaded, JS error, an old
                    // cached plugin without the signal, etc.
                    //
                    // Timed plugins use a safety cap. Plugins with duration 0
                    // are plugin-controlled: they should post signage:complete
                    // when done (YouTube full-video mode does this on ENDED).
                    const itemDur   = parseInt(item.duration || 0, 10);
                    const cfg = item.plugin.config || {};
                    const hasPluginVideoDuration = Object.prototype.hasOwnProperty.call(cfg, 'video_duration');
                    const pluginDur = parseInt(cfg.video_duration || 0, 10);
                    const baseDur = (hasPluginVideoDuration && pluginDur <= 0)
                        ? 0
                        : Math.max(itemDur, pluginDur);
                    const dur = baseDur > 0
                        ? (baseDur + 5) * 1_000
                        : 86_400_000; // 24h ceiling; plugin's complete signal wins
                    clearTimeout(slideTimer);
                    slideTimer = setTimeout(advance, dur);
                } else {
                    const dur = Math.max(1, (item.duration || 10)) * 1_000;
                    clearTimeout(slideTimer);
                    slideTimer = setTimeout(advance, dur);
                }
            });
        });
    }

    function advance() {
        paused = false;
        clearTimeout(slideTimer);
        // Synced groups: jump to whatever slide the wall-clock says is
        // current right now. Catches up displays that drifted (e.g. a
        // video ran 200ms long, the player skipped a beat). For unsynced
        // playback this is a plain +1.
        const tgt = syncedTarget();
        if (tgt) {
            showSlide(tgt.idx);
        } else {
            showSlide(currentIdx + 1);
        }
    }

    function manualNext() {
        paused = false;
        clearTimeout(slideTimer);
        if (items.length <= 1) {
            const vid = activeVideo();
            if (vid) {
                try {
                    const item = items[currentIdx] || {};
                    vid.currentTime = Math.max(0, item.clip_start || 0);
                    vid.play().catch(() => {});
                } catch {}
            }
            showMediaButtons();
            return;
        }
        advance();
    }

    function goBack() {
        paused = false;
        clearTimeout(slideTimer);
        showSlide(currentIdx - 1);
    }

    // Plugins signal completion via postMessage({ type: 'signage:complete' })
    // so the player advances immediately when the content is done rather than
    // waiting for the fallback duration timer.
    // While paused, completion signals are ignored so the playlist freezes
    // on the current slide regardless of what the plugin does internally.
    window.addEventListener('message', (e) => {
        if (!e.data || e.data.type !== 'signage:complete') return;
        if (paused) return;  // honor pause across plugin-driven advances too
        // Confirm the message came from the currently active iframe
        const activeFrame = activeSlide && activeSlide.querySelector('iframe');
        if (!activeFrame || e.source !== activeFrame.contentWindow) return;
        // Synced groups: ignore early-advance signals. The wall-clock
        // anchor is the only timekeeper -- letting plugins early-advance
        // would desync the group instantly.
        if (syncedTarget()) return;
        advance();
    });

    /* -----------------------------------------------------------------------
     * Media buttons — init event listeners
     * Listeners are ALWAYS attached; showMediaButtons() checks the live
     * SHOW_MEDIA_BUTTONS flag at runtime so toggling the setting takes
     * effect immediately without a page reload.
     * --------------------------------------------------------------------- */
    (function initMediaButtons() {
        if (!mediaBtns) return;

        // Cursor visibility is managed by applySettings on every settings change
        if (SHOW_MEDIA_BUTTONS) document.body.style.cursor = 'default';

        function updatePauseIcon() {
            if (btnPause) btnPause.innerHTML = paused ? '&#9654;' : '&#9646;&#9646;';
            if (btnPause) btnPause.title     = paused ? 'Resume' : 'Pause';
        }

        function togglePause() {
            paused = !paused;
            const vid = activeVideo();
            // Notify the active plugin iframe (if any) so well-behaved plugins
            // can pause their own animations / completion timers. The player
            // also ignores signage:complete while paused, so even plugins
            // that don't honor this message will still freeze on the slide.
            const activeFrame = activeSlide && activeSlide.querySelector('iframe');
            if (activeFrame && activeFrame.contentWindow) {
                try {
                    activeFrame.contentWindow.postMessage(
                        { type: paused ? 'signage:pause' : 'signage:resume' }, '*');
                } catch (_) {}
            }
            if (paused) {
                if (vid) vid.pause();
                clearTimeout(slideTimer);
            } else {
                if (vid) {
                    vid.play().catch(() => {});
                } else {
                    const item = items[currentIdx];
                    const dur = Math.max(1, (item.duration || 10)) * 1_000;
                    slideTimer = setTimeout(advance, dur);
                }
            }
            updatePauseIcon();
            showMediaButtons();
        }

        function scrubVideo(seconds) {
            const vid = activeVideo();
            if (!vid) return;
            vid.currentTime = Math.max(0, Math.min(vid.duration || 0, vid.currentTime + seconds));
            showMediaButtons();
        }

        // Show buttons on any mouse movement or touch anywhere on the page
        document.addEventListener('mousemove',  showMediaButtons);
        document.addEventListener('touchstart', showMediaButtons, { passive: true });

        // Hot-zone at the bottom of the screen catches mouse movement even
        // when an iframe (plugin) is on screen and consuming all events.
        const hotzone = document.getElementById('media-hotzone');
        if (hotzone) {
            hotzone.addEventListener('mousemove',  showMediaButtons);
            hotzone.addEventListener('touchstart', showMediaButtons, { passive: true });
        }

        // Keep the hide timer reset while hovering directly over the buttons
        mediaBtns.addEventListener('mouseenter', () => {
            clearTimeout(_mediaHideTimer);
        });
        mediaBtns.addEventListener('mouseleave', () => {
            _mediaHideTimer = setTimeout(hideMediaButtons, MEDIA_BTN_IDLE_MS);
        });

        if (btnPrev)   btnPrev.addEventListener('click',   (e) => { e.stopPropagation(); goBack();        showMediaButtons(); });
        if (btnNext)   btnNext.addEventListener('click',   (e) => { e.stopPropagation(); manualNext();    showMediaButtons(); });
        if (btnPause)  btnPause.addEventListener('click',  (e) => { e.stopPropagation(); togglePause();                       });
        if (btnRewind) btnRewind.addEventListener('click', (e) => { e.stopPropagation(); scrubVideo(-10); showMediaButtons(); });
        if (btnFfwd)   btnFfwd.addEventListener('click',   (e) => { e.stopPropagation(); scrubVideo(+10); showMediaButtons(); });

        // Keyboard shortcuts (only when kiosk input is unlocked)
        document.addEventListener('keydown', (e) => {
            if (isLocked() || _pinKeypadOpen) return;
            if (e.key === 'ArrowRight'  || e.key === 'MediaTrackNext')     { manualNext();    showMediaButtons(); }
            if (e.key === 'ArrowLeft'   || e.key === 'MediaTrackPrevious') { goBack();        showMediaButtons(); }
            if (e.key === ' '           || e.key === 'MediaPlayPause')     { togglePause();                       }
            if (e.key === 'ArrowUp')                                        { scrubVideo(+10);                     }
            if (e.key === 'ArrowDown')                                      { scrubVideo(-10);                     }
        });
    })();

    /* -----------------------------------------------------------------------
     * Playlist loading / reloading
     * --------------------------------------------------------------------- */
    function applyPlaylist(data, fromCache = false) {
        if (!data || !data.items || !data.items.length) {
            showOverlay('No content scheduled.', false);
            return;
        }
        const versionChanged = !!(data.version && data.version !== lastPlaylistVersion);
        if (data.version) lastPlaylistVersion = data.version;

        if (playlistHasExpiredUrls(data)) {
            fetchPlaylist();
            return;
        }

        if (!fromCache) savePlaylistCache(data);
        showOfflineBanner(fromCache, fromCache ? _networkState : 'online');

        clearPreloadedSlide();
        warmPlaylistCaches(data, fromCache, versionChanged);

        const wasEmpty = !items.length;
        playlist = data;
        items    = data.items;

        // Synchronized playback: wall-clock anchor + per-item durations.
        // Clock offset is calibrated via /server_time (not the HTML snapshot).
        if (data.sync && data.sync.enabled) {
            syncBlock = data.sync;
            console.log('[sync] enabled: anchor=' + data.sync.anchor_unix_ms +
                        ' cycle=' + data.sync.cycle_total_ms + 'ms');
            startSyncClockMaintenance();
        } else {
            syncBlock = null;
            stopSyncClockMaintenance();
        }

        if (wasEmpty) {
            // Nothing was playing — start immediately
            clearTimeout(slideTimer);
            // For synced groups, jump straight to the slide everyone else
            // is on right now. For unsynced, start at slide 0 like before.
            const tgt = syncedTarget();
            currentIdx = tgt ? tgt.idx : 0;
            hideOverlay();
            showSlide(currentIdx);
            preloadNext();
        } else {
            // Already playing: on playlist/schedule change always repaint so
            // new media URLs load (old slide DOM may still show prior files).
            const tgt = syncedTarget();
            const needJump = versionChanged ||
                (tgt && tgt.idx !== currentIdx);
            if (needJump) {
                if (tgt) {
                    console.log(`[sync] re-aligning: ${currentIdx} -> ${tgt.idx}` +
                        (versionChanged ? ' (playlist changed)' : ''));
                    currentIdx = tgt.idx;
                } else {
                    currentIdx = currentIdx % items.length;
                }
                clearTimeout(slideTimer);
                hideOverlay();
                showSlide(currentIdx);
            } else {
                currentIdx = currentIdx % items.length;
                hideOverlay();
            }
            preloadNext();
        }
    }

    async function fetchPlaylist() {
        try {
            const qs = clientQuery();
            const r = await fetch(`/api/display/${TOKEN}/playlist${qs ? '?' + qs : ''}`);
            if (r.status === 409) {
                handleBlocked();
                return;
            }
            if (!r.ok) {
                showOverlay('No content scheduled.', false);
                return;
            }
            const json = await r.json();
            if (json.status === 'success') {
                const pl = json.playlist;
                if (pl && pl.sync && pl.sync.enabled) {
                    await calibrateServerClock(3);
                }
                applyPlaylist(pl);
            } else {
                showOverlay(json.message || 'No content scheduled.', false);
            }
        } catch (e) {
            // Network error — try playing from cache
            const cached = loadPlaylistCache();
            if (cached && !items.length) {
                // Nothing playing yet — boot from cache
                applyPlaylist(cached, true);
            } else if (cached && items.length) {
                // Already playing — keep going and warm any missing media.
                showOfflineBanner(true, _networkState);
                warmPlaylistCaches(cached, true);
            } else {
                showOverlay('Server unreachable — no cached playlist available.', false);
            }
            // Keep retrying in the background
            setTimeout(fetchPlaylist, 30_000);
        }
    }

    /* -----------------------------------------------------------------------
     * Blocked (single-client conflict)
     * --------------------------------------------------------------------- */
    function handleBlocked() {
        blocked = true;
        clearTimeout(slideTimer);
        clearInterval(pingTimer);
        if (sseSource) { sseSource.close(); sseSource = null; }

        // Replace page content with blocked message
        document.body.innerHTML = `
            <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
                        height:100vh;background:#111;color:#ccc;font-family:sans-serif;gap:1.5rem;padding:2rem;text-align:center;">
                <svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" fill="#e55" viewBox="0 0 16 16">
                    <path d="M8 15A7 7 0 1 1 8 1a7 7 0 0 1 0 14zm0 1A8 8 0 1 0 8 0a8 8 0 0 0 0 16z"/>
                    <path d="M7.002 11a1 1 0 1 1 2 0 1 1 0 0 1-2 0zM7.1 4.995a.905.905 0 1 1 1.8 0l-.35 3.507a.552.552 0 0 1-1.1 0L7.1 4.995z"/>
                </svg>
                <h2 style="color:#e55;font-size:1.4rem;font-weight:600">Display Already Connected</h2>
                <p style="max-width:400px;line-height:1.6;color:#999;">
                    Another browser session is already displaying this screen.<br>
                    Close the other session, then reload this page to take over.
                </p>
                <p id="retry-msg" style="color:#666;font-size:0.9rem">Retrying in <span id="countdown">30</span>s…</p>
            </div>`;

        let secs = 30;
        const cd = document.getElementById('countdown');
        const iv = setInterval(() => {
            secs--;
            if (cd) cd.textContent = secs;
            if (secs <= 0) {
                clearInterval(iv);
                location.reload();
            }
        }, 1_000);
    }

    /* -----------------------------------------------------------------------
     * SSE — Server-Sent Events for instant push
     * --------------------------------------------------------------------- */
    function connectSSE() {
        if (blocked) return;
        if (sseSource) { sseSource.close(); }

        const qs = clientQuery();
        const url = `/display/${TOKEN}/events${qs ? '?' + qs : ''}`;
        sseSource = new EventSource(url);

        sseSource.addEventListener('reload', async (e) => {
            sseRetryDelay = SSE_BASE; // reset backoff on successful message
            try {
                const data = JSON.parse(e.data);
                if (data.playlist) {
                    const pl = data.playlist;
                    if (pl.sync && pl.sync.enabled) {
                        // Multi-sample so the new anchor lands on a clean
                        // offset (previously single-shot, which was the
                        // common source of cross-client drift after a
                        // playlist edit).
                        await calibrateServerClock(3);
                    }
                    applyPlaylist(pl);
                } else {
                    fetchPlaylist();
                }
            } catch {
                fetchPlaylist();
            }
        });

        sseSource.addEventListener('emergency', (e) => {
            sseRetryDelay = SSE_BASE;
            try {
                const b = JSON.parse(e.data);
                showEmergency(b);
            } catch {}
        });

        sseSource.addEventListener('emergency_clear', () => {
            // Only clear if the server explicitly says so — never auto-clear on SSE drop
            clearEmergency();
        });

        sseSource.addEventListener('settings', (e) => {
            sseRetryDelay = SSE_BASE;
            try {
                const s = JSON.parse(e.data);
                applySettings(s);
            } catch {}
        });

        // Plugin policy changed for this tenant. Forward to every visible
        // plugin iframe via postMessage so the plugin can re-init, then
        // reload the matching iframe so the new sandbox/CSP take effect
        // (sandbox+csp can only be (re)applied at iframe load time).
        sseSource.addEventListener('plugin_policy', (e) => {
            sseRetryDelay = SSE_BASE;
            let body = {};
            try { body = JSON.parse(e.data || '{}'); } catch {}
            const key = (body.plugin_key || '').toLowerCase();
            if (!key) return;
            console.log('[player] plugin_policy changed:', key, body);
            const frames = document.querySelectorAll('iframe[data-plugin-type], iframe[data-plugin-key]');
            frames.forEach(f => {
                const ftype = (f.dataset.pluginType || '').toLowerCase();
                const fkey  = (f.dataset.pluginKey  || '').toLowerCase();
                if (ftype !== key && fkey !== key) return;
                try {
                    f.contentWindow && f.contentWindow.postMessage({
                        type: 'signage:plugin_policy',
                        plugin_key: key,
                        enabled: !!body.enabled,
                        granted_permissions: body.granted_permissions || [],
                    }, '*');
                } catch {}
                if (!body.enabled) return;          // disabled plugins will be hidden on next playlist refresh
                try { f.src = f.src; } catch {}     // force reload to re-evaluate sandbox/CSP
            });
        });

        // Admin-pushed one-off commands (reload / reboot / update).
        // For 'reload' we just reload the page. For 'reboot' / 'update' we
        // forward the command to the Electron host via window.signage.
        // Browser-only clients can only honor 'reload' — the others fall back
        // to reload as a graceful no-op so something visible still happens.
        sseSource.addEventListener('command', (e) => {
            sseRetryDelay = SSE_BASE;
            let body = {};
            try { body = JSON.parse(e.data || '{}'); } catch {}
            const action = (body.action || '').toLowerCase();
            console.log('[player] command received:', action);

            if (action === 'reload') {
                // Hard reload the page
                setTimeout(() => window.location.reload(), 200);
                return;
            }

            // 'reboot' and 'update' need the Electron bridge
            const host = (typeof window.signage === 'object') ? window.signage : null;
            if (host && typeof host.runCommand === 'function') {
                try { host.runCommand(action, body); }
                catch (err) { console.warn('[player] host.runCommand failed:', err); }
            } else {
                console.warn('[player] command "' + action + '" requires Electron client; falling back to reload.');
                setTimeout(() => window.location.reload(), 200);
            }
        });

        sseSource.addEventListener('disconnect', (e) => {
            sseSource.close();
            sseSource = null;
            try {
                const data = JSON.parse(e.data || '{}');
                if (data.reason === 'superseded') {
                    handleBlocked();
                    return;
                }
            } catch {}
            // Reconnect after delay
            setTimeout(connectSSE, sseRetryDelay);
            sseRetryDelay = Math.min(sseRetryDelay * 2, SSE_MAX);
        });

        sseSource.onerror = () => {
            if (blocked) return;
            sseSource.close();
            sseSource = null;
            // Do NOT clear any active emergency on SSE drop — offline lock rule:
            // emergency stays on screen until an explicit emergency_clear event arrives.
            // Brief SSE blips are common; wait before declaring the server offline.
            if (_sseOfflineTimer) clearTimeout(_sseOfflineTimer);
            _sseOfflineTimer = setTimeout(() => {
                _sseOfflineTimer = null;
                setNetworkState('server-offline');
            }, SSE_OFFLINE_GRACE_MS);
            ping();
            setTimeout(connectSSE, sseRetryDelay);
            sseRetryDelay = Math.min(sseRetryDelay * 2, SSE_MAX);
        };

        sseSource.onopen = () => {
            if (_sseOfflineTimer) {
                clearTimeout(_sseOfflineTimer);
                _sseOfflineTimer = null;
            }
            sseRetryDelay = SSE_BASE;
            _pingFailStreak = 0;
            setNetworkState('online');
            // SSE reopened -> we have server reachability again. Network
            // outages plus wake-from-suspend are when local clocks
            // diverge the most, so refresh the offset with multiple
            // samples right after reconnect.
            calibrateServerClock(3);
        };
    }

    /* -----------------------------------------------------------------------
     * Ping loop — keeps display "online" in admin UI
     * Also tracks server reachability and broadcasts offline state to all
     * plugin iframes so they can switch to cached data without waiting for
     * their own network requests to time out.
     * --------------------------------------------------------------------- */

    function broadcastOnlineState(offline, state) {
        // Send to all plugin iframes currently in the DOM
        document.querySelectorAll('iframe').forEach(f => {
            try {
                f.contentWindow.postMessage({
                    type: 'signage:online_state',
                    offline: offline,
                    state:   state || (offline ? 'server-offline' : 'online')
                }, '*');
            } catch (_) {}
        });
    }

    /**
     * Update the network connectivity state. Called by the ping loop.
     * `state` is one of 'online' | 'server-offline' | 'network-offline'.
     */
    function setNetworkState(state) {
        if (_networkState === state) return;
        const wasOffline = _serverOffline;
        _networkState              = state;
        _serverOffline             = (state !== 'online');
        window.SIGNAGE_OFFLINE     = _serverOffline;
        window.SIGNAGE_NETWORK_STATE = state;
        broadcastOnlineState(_serverOffline, state);
        showOfflineBanner(_serverOffline, state);
        diagLog('net', 'state', 'network state', { state: state, was_offline: wasOffline });
        // Transitioning back online: refresh the server clock offset with
        // a multi-sample calibration. While we were offline both the
        // server clock and our OS clock kept ticking independently, so
        // the cached offset is almost certainly stale.
        if (wasOffline && !_serverOffline) {
            calibrateServerClock(3);
            // Also flush any diagnostics that piled up during the outage.
            flushDiagnostics();
        }
    }

    /** Backwards-compat shim — older code calls setServerOffline(bool). */
    function setServerOffline(offline) {
        setNetworkState(offline ? 'server-offline' : 'online');
    }

    // When new iframes are added (slide changes), broadcast current state to them
    function notifyIframeOfState(slide) {
        if (!slide) return;
        const frame = slide.querySelector('iframe');
        if (!frame) return;
        // Wait for iframe to load before sending
        frame.addEventListener('load', () => {
            try {
                frame.contentWindow.postMessage({
                    type: 'signage:online_state',
                    offline: _serverOffline,
                    state:   _networkState
                }, '*');
                frame.contentWindow.postMessage({
                    type: 'signage:clock_offset',
                    offsetMs: serverClockOffsetMs,
                    serverNowMs: localToServerNowMs()
                }, '*');
            } catch (_) {}
        });
    }

    /**
     * Push the latest server clock offset to every plugin iframe currently
     * on screen. Called after a successful /server_time calibration so
     * clock/marquee/dashboard plugins can re-render with corrected time.
     */
    function broadcastClockOffsetToFrames() {
        const frames = document.querySelectorAll('#player iframe');
        const offset = serverClockOffsetMs;
        const serverNow = localToServerNowMs();
        frames.forEach(frame => {
            try {
                frame.contentWindow && frame.contentWindow.postMessage({
                    type: 'signage:clock_offset',
                    offsetMs: offset,
                    serverNowMs: serverNow
                }, '*');
            } catch (_) {}
        });
    }

    /**
     * Probe the wider internet to distinguish "our server is down" from
     * "all network is down". Uses a tiny well-known image from a CDN that
     * supports CORS-less no-cors GETs. We don't care about the response
     * body, only whether the request resolves.
     */
    async function probeInternet() {
        const PROBES = [
            'https://www.gstatic.com/generate_204',
            'https://www.cloudflare.com/cdn-cgi/trace',
            'https://1.1.1.1/cdn-cgi/trace'
        ];
        for (const url of PROBES) {
            try {
                await fetch(url, { mode: 'no-cors', cache: 'no-store',
                                   signal: AbortSignal.timeout(3000) });
                return true;   // any one succeeded → we have internet
            } catch (_) { /* try next */ }
        }
        return false;
    }

    // ──────────────────────────────────────────────────────────────────
    // Proof of Play (Phase 4, optional)
    //
    // We track when the current slide became active and queue one
    // {item, started_at, duration_ms, completed} record on transition.
    // Records are flushed in batches so a chatty playlist doesn't beat
    // the server up; on disable the server returns {status:'disabled'}
    // and we go quiet until the next probe.
    // ──────────────────────────────────────────────────────────────────
    let _popQueue        = [];
    let _popServerOff    = false;       // server says feature disabled
    let _popCurrentItem  = null;
    let _popCurrentStart = 0;

    function proofOfPlayMark(completed) {
        const item = _popCurrentItem;
        const started = _popCurrentStart;
        // Reset for the slide we're about to show.
        _popCurrentItem  = items[currentIdx] || null;
        _popCurrentStart = Date.now();
        if (!item || !started || _popServerOff) return;
        const dur = Date.now() - started;
        if (dur < 250) return;          // ignore flicker
        _popQueue.push({
            started_at:  new Date(started).toISOString(),
            duration_ms: dur,
            completed:   !!completed,
            item_type:   item.type   || (item.plugin ? 'plugin' : null),
            item_name:   item.name   || (item.plugin && item.plugin.name) || item.content_url || null,
            media_id:    (item.media && item.media.id) || item.media_id || null,
            playlist_id: item.playlist_id || null,
            plugin_key:  (item.plugin && (item.plugin.key || item.plugin.type)) || null,
        });
        if (_popQueue.length >= 10) proofOfPlayFlush();
    }

    async function proofOfPlayFlush() {
        if (!_popQueue.length || _popServerOff) return;
        const batch = _popQueue.splice(0, _popQueue.length);
        try {
            const r = await fetch(`/api/display/${TOKEN}/proof-of-play`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ events: batch }),
            });
            if (r.ok) {
                const j = await r.json().catch(() => ({}));
                if (j.status === 'disabled') _popServerOff = true;
            } else {
                // Re-queue at the front so we don't lose data on transient errors
                _popQueue.unshift(...batch);
            }
        } catch {
            _popQueue.unshift(...batch);
        }
    }

    // Periodic flush so low-volume players still upload promptly.
    setInterval(proofOfPlayFlush, 30_000);
    // Last-gasp flush on navigation away.
    window.addEventListener('beforeunload', () => {
        if (!_popQueue.length || _popServerOff) return;
        try {
            navigator.sendBeacon(
                `/api/display/${TOKEN}/proof-of-play`,
                new Blob([JSON.stringify({ events: _popQueue })],
                         { type: 'application/json' }));
        } catch {}
    });

    async function ensureReportedAppVersion() {
        if (window.AISIGNX_APP_VERSION) return;
        if (typeof window.signage === 'object' &&
            typeof window.signage.getAppVersion === 'function') {
            try {
                const v = await window.signage.getAppVersion();
                if (v) window.AISIGNX_APP_VERSION = String(v);
            } catch (_) {}
        }
    }

    async function ping() {
        if (blocked) return;
        await ensureReportedAppVersion();
        const item = items[currentIdx];
        const current_content = item
            ? (item.plugin ? `Plugin: ${item.plugin.name}` : (item.name || item.content_url || ''))
            : '';
        // Reported back to the server so the displays page can show what
        // the client is actually running (browser kiosks: SW cache name;
        // Electron / native shells override window.AISIGNX_APP_VERSION).
        const app_version = window.AISIGNX_APP_VERSION
            || (window.__SW_VERSION__ ? 'browser-' + window.__SW_VERSION__ : 'browser');
        try {
            const r = await fetch(`/display/${TOKEN}/ping`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ current_content, app_version, client_id: CLIENT_ID })
            });
            if (r.status === 409) {
                handleBlocked();
                return;
            }
            // Ping succeeded — server is online
            _pingFailStreak = 0;
            setNetworkState('online');
        } catch {
            _pingFailStreak++;
            if (_pingFailStreak < PING_FAILS_BEFORE_OFFLINE) return;
            if (typeof window.AISignXNative !== 'undefined') {
                setNetworkState('server-offline');
                return;
            }
            // Ping failed — figure out whether the wider internet is up too
            const internetUp = await probeInternet();
            setNetworkState(internetUp ? 'server-offline' : 'network-offline');
        }
    }

    function startPingLoop() {
        clearInterval(pingTimer);
        pingTimer = setInterval(ping, PING_EVERY);
        // Also do a faster recovery ping every 10s when offline
        setInterval(() => {
            if (_serverOffline) ping();
        }, 10_000);
    }

    /* -----------------------------------------------------------------------
     * Emergency Broadcast overlay
     * Rules:
     *  - Full-screen takeover, z-index above everything
     *  - Stays locked on screen even if SSE drops (offline lock)
     *  - Removed ONLY on explicit emergency_clear SSE event
     *  - Audio alert tone loops while emergency is active
     *  - Distance-safe fonts (clamp-based, readable from across a room)
     * --------------------------------------------------------------------- */
    let emergencyOverlay  = null;
    let emergencyAudioCtx = null;
    let emergencyAudioInterval = null;

    const LEVEL_STYLES = {
        critical: { bg: '#b71c1c', fg: '#ffffff', icon: '&#128680;' },  // red siren
        warning:  { bg: '#e65100', fg: '#ffffff', icon: '&#9888;&#65039;' },  // amber warning
        info:     { bg: '#1565c0', fg: '#ffffff', icon: '&#8505;&#65039;' },  // blue info
    };

    function _playAlertTone() {
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            // Two-tone alert: 880 Hz then 660 Hz, 0.3s each
            const schedule = [[880, 0, 0.3], [660, 0.35, 0.3]];
            schedule.forEach(([freq, start, dur]) => {
                const osc  = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.frequency.value = freq;
                osc.type = 'sine';
                gain.gain.setValueAtTime(0.4, ctx.currentTime + start);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + start + dur);
                osc.start(ctx.currentTime + start);
                osc.stop(ctx.currentTime + start + dur + 0.05);
            });
            return ctx;
        } catch (_) { return null; }
    }

    function showEmergency(b) {
        if (emergencyOverlay) {
            // Already showing — update content in place (re-activate same id is fine)
            emergencyOverlay.remove();
            emergencyOverlay = null;
        }

        const level  = (b.level || 'critical').toLowerCase();
        const style  = LEVEL_STYLES[level] || LEVEL_STYLES.critical;
        const bg     = b.background_color || style.bg;
        const fg     = b.text_color       || style.fg;
        const icon   = style.icon;
        const ts     = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const author = b.created_by ? `Issued by: ${_esc(b.created_by)}` : 'AISignX Emergency Alert System';

        const el = document.createElement('div');
        el.id = 'emergency-overlay';
        el.setAttribute('role', 'alert');
        el.setAttribute('aria-live', 'assertive');
        el.style.cssText = [
            'position:fixed', 'inset:0', 'z-index:99999',
            `background:${bg}`,
            `color:${fg}`,
            'display:flex', 'flex-direction:column',
            'align-items:center', 'justify-content:center',
            'text-align:center',
            'padding:4vw',
            'font-family:"Arial Black",Arial,sans-serif',
            'box-sizing:border-box',
        ].join(';');

        el.innerHTML = `
            <div style="font-size:clamp(3rem,10vw,8rem);line-height:1;margin-bottom:2vh;">${icon}</div>
            <div style="
                font-size:clamp(2rem,7vw,6rem);
                font-weight:900;
                text-transform:uppercase;
                letter-spacing:0.06em;
                line-height:1.1;
                margin-bottom:3vh;
                text-shadow:0 2px 8px rgba(0,0,0,0.4);
                max-width:90vw;
            ">${_esc(b.title)}</div>
            ${b.message ? `
            <div style="
                font-size:clamp(1.2rem,4vw,3rem);
                font-weight:400;
                max-width:80vw;
                line-height:1.4;
                margin-bottom:4vh;
                font-family:Arial,sans-serif;
            ">${_esc(b.message)}</div>` : ''}
            <div style="
                position:absolute;
                bottom:3vh;
                left:0;right:0;
                display:flex;
                justify-content:space-between;
                padding:0 4vw;
                font-size:clamp(0.7rem,2vw,1.2rem);
                opacity:0.75;
                font-family:Arial,sans-serif;
            ">
                <span>${_esc(author)}</span>
                <span>Alert issued: ${ts}</span>
            </div>
        `;

        // Pulsing border animation
        const style_el = document.createElement('style');
        style_el.id = 'emergency-pulse-style';
        style_el.textContent = `
            #emergency-overlay {
                animation: emergency-pulse 2s ease-in-out infinite;
                border: 0.5vw solid rgba(255,255,255,0.3);
            }
            @keyframes emergency-pulse {
                0%,100% { border-color: rgba(255,255,255,0.3); }
                50%      { border-color: rgba(255,255,255,0.8); }
            }
        `;
        document.head.appendChild(style_el);
        document.body.appendChild(el);
        emergencyOverlay = el;

        // Pause any playing video/audio content
        document.querySelectorAll('video,audio').forEach(m => {
            try { m.pause(); } catch (_) {}
        });

        // Audio alert — play immediately then repeat every 8s
        if (emergencyAudioInterval) clearInterval(emergencyAudioInterval);
        emergencyAudioCtx = _playAlertTone();
        emergencyAudioInterval = setInterval(() => { _playAlertTone(); }, 8_000);
    }

    function clearEmergency() {
        if (emergencyOverlay) {
            emergencyOverlay.remove();
            emergencyOverlay = null;
        }
        const ps = document.getElementById('emergency-pulse-style');
        if (ps) ps.remove();
        if (emergencyAudioInterval) {
            clearInterval(emergencyAudioInterval);
            emergencyAudioInterval = null;
        }
        if (emergencyAudioCtx) {
            try { emergencyAudioCtx.close(); } catch (_) {}
            emergencyAudioCtx = null;
        }
    }

    function _esc(s) {
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    /* -----------------------------------------------------------------------
     * Boot
     * --------------------------------------------------------------------- */
    // Visibility recovery: when a tab/window becomes visible again
    // (especially in browsers where background tab timers throttle to ~1/min,
    // or when a Chromium kiosk has been brought back from screen-off),
    // immediately re-calibrate the server clock and snap to the right slide.
    // This applies even with no sync group -- the clock plugin needs a
    // fresh offset, not the one from when the tab went to sleep an hour ago.
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;
        calibrateServerClock(3).then(() => tickSyncDriftWatchdog())
                               .catch(() => tickSyncDriftWatchdog());
    });

    (async function bootPlayer() {
        showOverlay('Loading…', true);

        const hasBoot = !!(BOOT && BOOT.items && BOOT.items.length);
        const cached = !hasBoot ? loadPlaylistCache() : null;

        // On cold boot, always do a 3-sample calibration BEFORE painting
        // anything that depends on server time -- both synced groups and
        // the clock plugin. We need this to happen synchronously so the
        // very first frame of the clock plugin already reflects server
        // time, not OS time. If the server is unreachable the cached
        // offset from loadPlaylistCache() keeps us going.
        await calibrateServerClock(3);

        if (hasBoot) {
            applyPlaylist(BOOT);
        } else if (cached && cached.items && cached.items.length) {
            applyPlaylist(cached, true);
            fetchPlaylist();
        } else {
            fetchPlaylist();
        }

        connectSSE();
        await ensureReportedAppVersion();
        startPingLoop();
        ping(); // immediate ping to mark online
        // Electron minimize / task switch pauses media without unloading the page.
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState !== 'visible') return;
            _pingFailStreak = 0;
            if (_sseOfflineTimer) {
                clearTimeout(_sseOfflineTimer);
                _sseOfflineTimer = null;
            }
            ping();
            resumePlayback();
        });
        // Always-on background recalibration, regardless of group sync.
        // The 60s cadence keeps the clock plugin within ~50-100ms across
        // a fleet even when OS clocks drift independently.
        startClockRecalibration();
        reportCapabilities();
        // Signed /uploads/ URLs expire; refresh in the background so long
        // runs do not end up on broken-image placeholders.
        setInterval(() => { refreshPlaylistUrls(); }, 45 * 60 * 1000);
    })();

    /* -----------------------------------------------------------------------
     * PIN keypad UI
     *
     * Built lazily on first showPinKeypad() call so a kiosk with no PIN
     * never pays the DOM cost. Once built, kept in the document and
     * shown/hidden by toggling display:flex/none.
     *
     * The keypad is a small centered modal: dimmed full-screen backdrop +
     * an 8-character PIN display + a 0-9 / clear / submit button grid +
     * a tiny "X" close button. Tapping outside the keypad closes it
     * without unlocking.
     * --------------------------------------------------------------------- */
    let _pinKeypadOpen   = false;
    let _pinKeypadEl     = null;
    let _pinDisplayEl    = null;
    let _pinErrorEl      = null;
    let _pinHideTimer    = null;
    let _pinEntered      = '';
    let _pinCloseBtn     = null;
    let _pinKeyListener  = null;

    function pinKeypadButtons() {
        if (!_pinKeypadEl) return [];
        return Array.from(_pinKeypadEl.querySelectorAll('button'));
    }

    function touchPinKeypadTimer() {
        if (_pinHideTimer) clearTimeout(_pinHideTimer);
        _pinHideTimer = setTimeout(closePinKeypad, 30_000);
    }

    function focusPinKeypadButton(delta) {
        const buttons = pinKeypadButtons();
        if (!buttons.length) return;
        const active = document.activeElement;
        let idx = buttons.indexOf(active);
        if (idx < 0) {
            buttons[0].focus();
            return;
        }
        idx = (idx + delta + buttons.length) % buttons.length;
        buttons[idx].focus();
        touchPinKeypadTimer();
    }

    function activateFocusedPinButton() {
        const active = document.activeElement;
        if (active && active.tagName === 'BUTTON' && _pinKeypadEl && _pinKeypadEl.contains(active)) {
            active.click();
            return true;
        }
        return false;
    }

    function handlePinKeypadKeydown(e) {
        if (!_pinKeypadOpen) return;
        const key = e.key;
        if (key >= '0' && key <= '9') {
            e.preventDefault();
            e.stopPropagation();
            onPinDigit(key);
            touchPinKeypadTimer();
            return;
        }
        if (key === 'Backspace' || key === 'Delete') {
            e.preventDefault();
            e.stopPropagation();
            onPinClear();
            touchPinKeypadTimer();
            return;
        }
        if (key === 'Escape' || key === 'BrowserBack') {
            e.preventDefault();
            e.stopPropagation();
            closePinKeypad();
            return;
        }
        if (key === 'ArrowRight' || key === 'ArrowDown') {
            e.preventDefault();
            e.stopPropagation();
            focusPinKeypadButton(1);
            return;
        }
        if (key === 'ArrowLeft' || key === 'ArrowUp') {
            e.preventDefault();
            e.stopPropagation();
            focusPinKeypadButton(-1);
            return;
        }
        if (key === 'Enter' || key === 'NumpadEnter' || key === 'Select') {
            e.preventDefault();
            e.stopPropagation();
            if (!activateFocusedPinButton()) onPinSubmit();
            touchPinKeypadTimer();
        }
    }

    function buildPinKeypad() {
        if (_pinKeypadEl) return;
        const kp = document.createElement('div');
        kp.id = 'pin-keypad';
        kp.style.cssText = [
            'position:fixed','inset:0','z-index:2147483647',
            'display:none','align-items:center','justify-content:center',
            'background:rgba(0,0,0,0.85)',
            'opacity:0','transition:opacity 150ms ease-out',
            'font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif',
            'color:#fff','user-select:none','-webkit-user-select:none',
            'touch-action:manipulation'
        ].join(';');

        const card = document.createElement('div');
        card.style.cssText = [
            'background:#1f2937','border-radius:14px','padding:1.5rem 1.75rem',
            'box-shadow:0 25px 60px rgba(0,0,0,0.7)','min-width:340px',
            'display:flex','flex-direction:column','gap:1rem',
            'align-items:stretch','border:2px solid #475569'
        ].join(';');

        const titleRow = document.createElement('div');
        titleRow.style.cssText = 'display:flex;align-items:center;justify-content:space-between;';
        const title = document.createElement('div');
        title.textContent = 'Enter PIN';
        title.style.cssText = 'font-size:1.2rem;font-weight:600;';
        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.tabIndex = 0;
        closeBtn.textContent = 'Close';
        closeBtn.style.cssText = [
            'background:#475569','color:#fff','border:0','border-radius:6px',
            'padding:0.4rem 0.9rem','font-size:0.9rem','cursor:pointer'
        ].join(';');
        closeBtn.addEventListener('click', (e) => { e.stopPropagation(); closePinKeypad(); });
        _pinCloseBtn = closeBtn;
        titleRow.appendChild(title);
        titleRow.appendChild(closeBtn);

        const display = document.createElement('div');
        display.style.cssText = [
            'background:#0f172a','border-radius:8px','padding:0.75rem',
            'font-size:1.7rem','letter-spacing:0.5rem','text-align:center',
            'min-height:1.6em','font-family:monospace'
        ].join(';');
        _pinDisplayEl = display;

        const err = document.createElement('div');
        err.style.cssText = 'min-height:1.2em;color:#f87171;font-size:0.9rem;text-align:center;';
        _pinErrorEl = err;

        const grid = document.createElement('div');
        grid.style.cssText = [
            'display:grid','grid-template-columns:repeat(3,1fr)','gap:0.6rem'
        ].join(';');
        const isTvShell = (window.AISIGNX_NATIVE_CLIENT === 'android');
        const btnStyle = [
            'padding:' + (isTvShell ? '1.35rem 0' : '1.1rem 0'),
            'background:#334155','color:#fff','border:0',
            'border-radius:8px','font-size:' + (isTvShell ? '1.55rem' : '1.4rem'),
            'cursor:pointer','touch-action:manipulation',
            'min-width:' + (isTvShell ? '4.5rem' : 'auto'),
            'min-height:' + (isTvShell ? '3.25rem' : 'auto')
        ].join(';');
        const focusStyle = 'outline:3px solid #38bdf8;outline-offset:2px';
        function makeBtn(label, onClick) {
            const b = document.createElement('button');
            b.type = 'button';
            b.tabIndex = 0;
            b.textContent = label;
            b.style.cssText = btnStyle;
            b.addEventListener('focus', () => { b.style.cssText = btnStyle + ';' + focusStyle; });
            b.addEventListener('blur', () => { b.style.cssText = btnStyle; });
            b.addEventListener('click', (e) => { e.stopPropagation(); onClick(); touchPinKeypadTimer(); });
            // Stop pointerdown so the long-press handler doesn't re-arm
            // a fresh timer while the user is tapping the keypad.
            b.addEventListener('pointerdown', (e) => e.stopPropagation());
            return b;
        }
        // 1 2 3 / 4 5 6 / 7 8 9 / clear 0 enter
        ['1','2','3','4','5','6','7','8','9'].forEach(d => {
            grid.appendChild(makeBtn(d, () => onPinDigit(d)));
        });
        grid.appendChild(makeBtn('Clear', onPinClear));
        grid.appendChild(makeBtn('0',     () => onPinDigit('0')));
        grid.appendChild(makeBtn('Enter', onPinSubmit));

        card.appendChild(titleRow);
        card.appendChild(display);
        card.appendChild(err);
        if (isTvShell) {
            const hint = document.createElement('div');
            hint.style.cssText = 'font-size:0.85rem;color:#94a3b8;text-align:center;line-height:1.4;';
            hint.textContent = 'Remote: arrows move, OK selects, 0–9 enter PIN, Menu opens keypad';
            card.appendChild(hint);
        }
        card.appendChild(grid);
        kp.appendChild(card);

        // Stop bubbling on the backdrop AND card so clicks can't trigger
        // the global pointerdown handler (which would re-arm long-press
        // timers). We removed click-outside-to-close because users were
        // hitting the backdrop and dismissing the keypad before they
        // could enter the PIN -- use the explicit Close button instead.
        kp.addEventListener('pointerdown', (e) => e.stopPropagation());
        kp.addEventListener('click',       (e) => e.stopPropagation());

        document.body.appendChild(kp);
        _pinKeypadEl = kp;
    }

    function showPinKeypad(errorMsg) {
        buildPinKeypad();
        _pinEntered = '';
        renderPinDisplay();
        _pinErrorEl.textContent = errorMsg || '';
        _pinKeypadEl.style.display = 'flex';
        // Force a reflow so the next style change animates from scratch.
        // Without this the very first show after the kp is built skips
        // the fade-in because the browser batches the inserted node and
        // the opacity transition together.
        // eslint-disable-next-line no-unused-expressions
        _pinKeypadEl.offsetHeight;
        _pinKeypadEl.style.opacity = '1';
        _pinKeypadOpen = true;
        if (!_pinKeyListener) {
            _pinKeyListener = (e) => handlePinKeypadKeydown(e);
            window.addEventListener('keydown', _pinKeyListener, true);
        }
        // Auto-close after 30s of no interaction so the keypad doesn't
        // sit on screen forever if the admin walks away.
        touchPinKeypadTimer();
        try { console.log('[lock] keypad shown'); } catch (_) {}
        requestAnimationFrame(() => {
            const first = _pinCloseBtn || pinKeypadButtons()[0];
            if (first) first.focus();
        });
    }

    function closePinKeypad() {
        if (!_pinKeypadEl) return;
        _pinKeypadEl.style.display = 'none';
        _pinKeypadEl.style.opacity = '0';
        _pinKeypadOpen = false;
        _pinEntered = '';
        if (_pinKeyListener) {
            window.removeEventListener('keydown', _pinKeyListener, true);
            _pinKeyListener = null;
        }
        if (_pinHideTimer) { clearTimeout(_pinHideTimer); _pinHideTimer = null; }
    }

    function onPinDigit(d) {
        if (_pinEntered.length >= 8) return;
        _pinEntered += d;
        renderPinDisplay();
        // Auto-submit when length matches the configured PIN length.
        if (_pinEntered.length === UNLOCK_PIN.length) {
            // small delay so the last digit is visible before the modal closes
            setTimeout(onPinSubmit, 120);
        }
    }
    function onPinClear() {
        _pinEntered = '';
        _pinErrorEl.textContent = '';
        renderPinDisplay();
    }
    function onPinSubmit() {
        if (!UNLOCK_PIN) { closePinKeypad(); return; }
        if (_pinEntered === UNLOCK_PIN) {
            _unlockedUntil = Date.now() + UNLOCK_GRACE_MS;
            _pinFailCount  = 0;
            closePinKeypad();
            applyInputLock();
            // Re-enable iframe focus
            if (activeSlide) {
                activeSlide.querySelectorAll('iframe').forEach(f => { f.tabIndex = 0; });
            }
            // Brief on-screen confirmation
            flashUnlockedToast();
            // Ask the native shell (Electron / Android) to minimize so a
            // technician can reach the desktop. The shell keeps the kiosk
            // minimized until the OS goes idle for a few minutes or the user
            // brings the window back, then it re-asserts kiosk + tells us to
            // re-lock (see the relock handler near startup). In a plain
            // browser these bridges are absent and we just stay unlocked.
            requestShellMinimize();
            // Auto re-lock when the grace window expires.
            setTimeout(() => {
                if (Date.now() >= _unlockedUntil) {
                    applyInputLock();
                    if (activeSlide) {
                        activeSlide.querySelectorAll('iframe').forEach(f => { f.tabIndex = -1; });
                    }
                }
            }, UNLOCK_GRACE_MS + 100);
            return;
        }
        _pinFailCount++;
        _pinEntered = '';
        renderPinDisplay();
        if (_pinFailCount >= PIN_FAIL_LOCKOUT) {
            _pinLockedOutUntil = Date.now() + PIN_LOCKOUT_MS;
            _pinFailCount = 0;
            _pinErrorEl.textContent = 'Too many wrong tries. Locked for 60s.';
            setTimeout(closePinKeypad, 1500);
        } else {
            _pinErrorEl.textContent = 'Incorrect PIN.';
        }
    }
    function renderPinDisplay() {
        if (!_pinDisplayEl) return;
        _pinDisplayEl.textContent = _pinEntered.replace(/./g, '\u2022') || ' ';
    }

    function flashUnlockedToast() {
        const t = document.createElement('div');
        t.textContent = 'Unlocked for 5\u202fmin';
        t.style.cssText = [
            'position:fixed','top:1rem','left:50%','transform:translateX(-50%)',
            'background:rgba(16,185,129,0.95)','color:#fff','padding:0.5rem 1rem',
            'border-radius:999px','font:0.9rem system-ui,sans-serif','z-index:99998',
            'box-shadow:0 4px 12px rgba(0,0,0,0.4)','pointer-events:none'
        ].join(';');
        document.body.appendChild(t);
        setTimeout(() => { try { t.remove(); } catch (_) {} }, 1800);
    }

    // Expose for debugging from devtools
    window.signageLock = {
        isLocked: isLocked,
        unlockedUntil: () => _unlockedUntil,
        pin: () => UNLOCK_PIN,
        forceLock: () => { _unlockedUntil = 0; applyInputLock(); }
    };
    window.promptSignagePin = promptForPin;
    window.signageReportConnectivity = function(state) {
        if (state === 'online') {
            setNetworkState('online');
            return;
        }
        if (state === 'network-offline') {
            setNetworkState('network-offline');
            return;
        }
        setNetworkState('server-offline');
    };

    // Apply lock on boot
    applyInputLock();

})();
