// Setup window logic. Talks to the main process only through window.setupApi (preload).
(function () {
  'use strict';
  var api = window.setupApi;
  var $ = function (id) { return document.getElementById(id); };
  var server = $('server'), result = $('result'), card = $('trust-card'), trust = $('trust');
  var saveBtn = $('save'), testBtn = $('test'), forgetBtn = $('forget');
  var last = null;        // last test result: {serverUrl, result, ...}
  var lastInput = '';     // the text that was tested
  var saveAnyway = false; // second click on Save after an unreachable test
  var forgetArmed = false;

  function show(el, on) { el.hidden = !on; }
  function setResult(text, kind) {
    result.textContent = text || '';
    result.className = 'result' + (kind ? ' ' + kind : '');
  }
  function busy(on) {
    [saveBtn, testBtn, forgetBtn].forEach(function (b) { b.disabled = on; });
  }
  function resetTest() {
    last = null;
    saveAnyway = false;
    saveBtn.textContent = 'Save and start';
    show(card, false);
    show($('http-warning'), false);
    trust.checked = false;
  }

  function renderTest(r) {
    last = r;
    var kind = r.result === 'ok' ? 'ok' : (r.result === 'untrusted' ? 'warn' : 'err');
    setResult(r.message, kind);
    show($('http-warning'), !!r.httpWarning);
    if (r.card) {
      $('trust-title').textContent = r.card.pinMismatch ? 'Server certificate changed' : 'Certificate not trusted';
      $('fp').textContent = r.card.fingerprint;
      $('subject').textContent = r.card.subject || '';
      $('valid-to').textContent = r.card.validTo || '';
      show($('trust-label'), r.card.pinnable);
      show($('install-ca'), !r.card.pinnable);
      show(card, true);
    } else {
      show(card, false);
    }
  }

  function runTest() {
    var text = server.value.trim();
    resetTest();
    setResult('Testing...', '');
    busy(true);
    return api.testServer(text).then(function (r) {
      lastInput = text;
      renderTest(r);
      return r;
    }, function () {
      setResult('The test failed unexpectedly.', 'err');
      return null;
    }).then(function (r) { busy(false); return r; });
  }

  function doSave() {
    busy(true);
    setResult('Saving...', '');
    api.save({ serverUrl: server.value.trim(), trust: trust.checked, autostart: $('autostart').checked })
      .then(function (r) {
        busy(false);
        if (r && r.ok) setResult('Saved. Starting the player...', 'ok');
        else setResult((r && r.message) || 'Could not save.', 'err');
      }, function () { busy(false); setResult('Could not save.', 'err'); });
  }

  function onSave(ev) {
    ev.preventDefault();
    var text = server.value.trim();
    var ready = last && lastInput === text ? Promise.resolve(last) : runTest();
    ready.then(function (r) {
      if (!r || r.result === 'invalid') return;
      if (r.result === 'ok') { doSave(); return; }
      if (r.result === 'untrusted') {
        if (trust.checked) doSave();
        else setResult('Tick "Trust this certificate" after comparing the fingerprint, or install the CA.', 'warn');
        return;
      }
      if (saveAnyway) { doSave(); return; }
      saveAnyway = true;
      saveBtn.textContent = 'Save anyway';
      setResult(r.message + '. Click "Save anyway" to keep this address.', 'err');
    });
  }

  server.addEventListener('input', function () { if (server.value.trim() !== lastInput) resetTest(); });
  testBtn.addEventListener('click', function () { runTest(); });
  $('form').addEventListener('submit', onSave);
  $('close').addEventListener('click', function () { api.close(); });
  $('quit').addEventListener('click', function () { api.quit(); });
  forgetBtn.addEventListener('click', function () {
    if (!forgetArmed) {
      forgetArmed = true;
      forgetBtn.textContent = 'Click again to forget';
      setTimeout(function () { forgetArmed = false; forgetBtn.textContent = 'Forget server'; }, 5000);
      return;
    }
    busy(true);
    api.forget().then(function () {
      busy(false);
      server.value = '';
      resetTest();
      show(forgetBtn, false);
      setResult('Server forgotten. Pairing, cookies and caches were cleared.', 'ok');
    });
  });

  api.getConfig().then(function (c) {
    server.value = c.serverUrl || '';
    $('autostart').checked = c.autostart !== false;
    show(forgetBtn, !!c.serverUrl);
    $('meta').textContent = 'Version ' + c.version + '  |  Client ' + c.clientId + (c.port ? '  |  Local port ' + c.port : '');
    server.focus();
  });
})();
