// Offline page: served by the loopback proxy (/__shell/offline, or /player/ when there is no
// snapshot) and loaded as a local file when the proxy itself is down. Retries every 10 s.
(function () {
  'use strict';
  var RETRY_S = 10;
  var MESSAGES = {
    'unreachable': "Can't reach the AiSignX server. Check the network and the server.",
    'tls-pin-mismatch': 'Server certificate changed. Re-run setup (Ctrl+Alt+S).',
    'tls-untrusted': 'The server certificate is not trusted. Re-run setup (Ctrl+Alt+S).',
    'http': 'The server answered with an error.',
    'port-in-use': 'Local port in use. Close the program using it, or re-run setup (Ctrl+Alt+S).',
    'proxy-down': 'The player is restarting its local connection.',
    'offline': 'The server cannot be reached.'
  };
  var body = document.body;
  var params = new URLSearchParams(location.search);
  var reason = body.getAttribute('data-reason') || params.get('reason') || 'offline';
  var target = body.getAttribute('data-target') || params.get('target') || '';
  // Only ever go back to the player on the loopback proxy (or the same origin).
  if (!/^\/player\/$/.test(target) && !/^http:\/\/127\.0\.0\.1:\d{2,5}\/player\/$/.test(target)) target = '';

  document.getElementById('reason').textContent = MESSAGES[reason] || MESSAGES.offline;
  var left = RETRY_S;
  var countdown = document.getElementById('countdown');
  function tick() {
    countdown.textContent = 'Retrying in ' + left + ' s';
    if (left <= 0) {
      if (target) location.replace(target); else location.reload();
      return;
    }
    left -= 1;
    setTimeout(tick, 1000);
  }
  tick();
})();
