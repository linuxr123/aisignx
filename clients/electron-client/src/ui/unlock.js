'use strict';
// The PIN prompt. The PIN goes to the main process and is checked there.
(function () {
  const $ = (id) => document.getElementById(id);
  const api = window.unlockApi;
  const LABELS = { setup: 'Enter the unlock PIN to open setup', log: 'Enter the unlock PIN to open the log',
    devtools: 'Enter the unlock PIN to open developer tools', quit: 'Enter the unlock PIN to quit the player' };
  const action = new URLSearchParams(location.search).get('action');
  if (LABELS[action]) $('label').textContent = LABELS[action];

  let lockedUntil = 0;
  let timer = null;
  let busy = false;

  function fmt(ms) {
    const s = Math.ceil(ms / 1000);
    return s >= 60 ? Math.floor(s / 60) + ' min ' + String(s % 60).padStart(2, '0') + ' s' : s + ' s';
  }
  function tick() {
    const left = lockedUntil - Date.now();
    if (left <= 0) {
      clearInterval(timer);
      timer = null;
      $('msg').textContent = '';
      $('pin').disabled = $('ok').disabled = false;
      $('pin').focus();
      return;
    }
    $('msg').textContent = 'Too many wrong PINs. Try again in ' + fmt(left) + '.';
  }
  function lock(waitMs) {
    if (!(waitMs > 0)) return false;
    lockedUntil = Date.now() + waitMs;
    $('pin').disabled = $('ok').disabled = true;
    if (!timer) timer = setInterval(tick, 1000);
    tick();
    return true;
  }

  $('form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const pin = $('pin').value;
    if (busy || !pin) return;
    busy = true;
    $('ok').disabled = true;
    $('msg').textContent = 'Checking...';
    let r = null;
    try { r = await api.tryPin(pin); } catch (e) { r = null; }
    busy = false;
    $('pin').value = '';
    $('ok').disabled = false;
    if (r && r.ok) { $('msg').textContent = ''; return; } // main closes the window
    if (!r || !lock(r.waitMs)) {
      $('msg').textContent = r ? 'Wrong PIN.' : 'Could not check the PIN.';
      $('pin').focus();
    }
  });
  $('cancel').addEventListener('click', () => api.cancel());
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') api.cancel(); });

  api.status().then((s) => { if (!lock(s && s.waitMs)) $('pin').focus(); }).catch(() => {});
})();
