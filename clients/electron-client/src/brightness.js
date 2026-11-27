'use strict';
// The screen's real brightness on Windows, from the display settings (settings.brightness and the night
// window settings.dim). Twin of brightnessFor() in the server's static/player/device.js and of
// Brightness.kt in the Android app.
//
// One hidden PowerShell sets every built-in panel through WMI (WmiMonitorBrightnessMethods) and every
// external monitor that answers DDC/CI (dxva2.dll). The only variable in the script is the integer level,
// checked to be 10..100: nothing from the server is ever put into the command line.
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');

const CLOCK = /^([01]\d|2[0-3]):([0-5]\d)$/;
const STATE_FILE = 'brightness.json';
const PROBE_FILE = 'brightness-probe.json';
const TICK_MS = 60 * 1000;
const RUN_TIMEOUT_MS = 15 * 1000;
const RETRY_MS = 10 * 60 * 1000; // a failed level is tried again after this, not every minute
const PS_ARGS = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand'];

// ---- Pure rules.
function pct(v, d) { return typeof v === 'number' && Number.isFinite(v) ? Math.max(10, Math.min(100, Math.round(v))) : d; }
function cleanDim(d) {
  if (!d || typeof d !== 'object' || !CLOCK.test(d.from) || !CLOCK.test(d.to) || d.from === d.to) return null;
  return { level: pct(d.level, 100), from: d.from, to: d.to, tz: typeof d.tz === 'string' ? d.tz : '' };
}
function minutes(t) { const m = CLOCK.exec(t); return Number(m[1]) * 60 + Number(m[2]); }
// The minute of the day (0..1439) in zone tz at nowMs; the PC's own zone when the name is empty or unknown.
function minuteOfDay(nowMs, tz) {
  try {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: tz || undefined, hour: '2-digit', minute: '2-digit', hour12: false })
      .format(new Date(nowMs)).split(':');
    const m = (Number(parts[0]) % 24) * 60 + Number(parts[1]);
    if (Number.isFinite(m)) return m;
  } catch (e) { /* unknown zone */ }
  const d = new Date(nowMs);
  return d.getHours() * 60 + d.getMinutes();
}
// The level (10..100) to show at nowMs: the base, or the night level inside the window (which may cross midnight).
function levelAt(settings, nowMs) {
  const s = settings && typeof settings === 'object' ? settings : {};
  const base = pct(s.brightness, 100);
  const dim = cleanDim(s.dim);
  if (!dim) return base;
  const now = minuteOfDay(nowMs, dim.tz), from = minutes(dim.from), to = minutes(dim.to);
  const inside = from < to ? now >= from && now < to : now >= from || now < to;
  return inside ? Math.min(base, dim.level) : base;
}

// ---- The PowerShell script. level null = probe only (count what can be controlled, change nothing).
const DDC_CS = `
using System; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class AiSignXDdc {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  public struct PM { public IntPtr h; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string d; }
  delegate bool EnumProc(IntPtr hm, IntPtr hdc, IntPtr rc, IntPtr data);
  [DllImport("user32.dll")] static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, EnumProc cb, IntPtr data);
  [DllImport("dxva2.dll")] static extern bool GetNumberOfPhysicalMonitorsFromHMONITOR(IntPtr hm, out uint n);
  [DllImport("dxva2.dll")] static extern bool GetPhysicalMonitorsFromHMONITOR(IntPtr hm, uint n, [Out] PM[] a);
  [DllImport("dxva2.dll")] static extern bool GetMonitorBrightness(IntPtr h, out uint mn, out uint cur, out uint mx);
  [DllImport("dxva2.dll")] static extern bool SetMonitorBrightness(IntPtr h, uint v);
  [DllImport("dxva2.dll")] static extern bool DestroyPhysicalMonitors(uint n, PM[] a);
  public static int[] Run(int level) {
    var list = new List<IntPtr>();
    EnumProc cb = (hm, a, b, c) => { list.Add(hm); return true; };
    EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, cb, IntPtr.Zero);
    GC.KeepAlive(cb);
    int found = 0, set = 0;
    foreach (var hm in list) {
      uint n;
      if (!GetNumberOfPhysicalMonitorsFromHMONITOR(hm, out n) || n == 0) continue;
      var arr = new PM[n];
      if (!GetPhysicalMonitorsFromHMONITOR(hm, n, arr)) continue;
      try {
        foreach (var p in arr) {
          uint mn, cur, mx;
          if (!GetMonitorBrightness(p.h, out mn, out cur, out mx) || mx <= mn) continue;
          found++;
          if (level < 0) continue;
          if (SetMonitorBrightness(p.h, (uint)(mn + Math.Round((mx - mn) * level / 100.0)))) set++;
        }
      } finally { DestroyPhysicalMonitors(n, arr); }
    }
    return new int[] { found, set };
  }
}`;

function validLevel(level) {
  return Number.isInteger(level) && level >= 10 && level <= 100;
}

function script(level) {
  const probe = level === null;
  if (!probe && !validLevel(level)) throw new Error('bad brightness level');
  const L = probe ? -1 : level;
  return [
    "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'",
    '$L = ' + L,
    '$w = 0; $d = 0; $err = @()',
    'try {',
    '  foreach ($m in @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)) {',
    '    if ($L -lt 0) { $w++; continue }',
    '    try { Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{ Timeout = [uint32]0; Brightness = [byte]$L } | Out-Null; $w++ }',
    "    catch { $err += 'wmi: ' + $_.Exception.Message }",
    '  }',
    '} catch { }', // no WMI brightness class: a desktop PC
    'try {',
    "  if (-not ('AiSignXDdc' -as [type])) { Add-Type -TypeDefinition @'",
    DDC_CS.trim(),
    "'@ }",
    '  $r = [AiSignXDdc]::Run($L); $d = $(if ($L -lt 0) { $r[0] } else { $r[1] })',
    "} catch { $err += 'ddc: ' + $_.Exception.Message }",
    '$o = @{ wmi = $w; ddc = $d }',
    "if ($err.Count) { $o.error = ($err -join '; ').Substring(0, [Math]::Min(300, ($err -join '; ').Length)) }",
    '[Console]::Out.Write(($o | ConvertTo-Json -Compress))',
  ].join('\n');
}

function psArgs(level) {
  return PS_ARGS.concat([Buffer.from(script(level), 'utf16le').toString('base64')]);
}

function defaultRun(file, args, opts) {
  return new Promise((resolve) => {
    execFile(file, args, Object.assign({ windowsHide: true, maxBuffer: 64 * 1024 }, opts), (err, stdout, stderr) => {
      resolve({ ok: !err, out: String(stdout || ''), err: err ? String(stderr || err.message || '').slice(0, 300) : '' });
    });
  });
}

// {wmi, ddc, error?} from the script's output, or null.
function parseResult(out) {
  try {
    const o = JSON.parse(String(out || '').trim());
    if (!o || typeof o !== 'object') return null;
    const n = (v) => (Number.isInteger(v) && v >= 0 ? v : 0);
    const r = { wmi: n(o.wmi), ddc: n(o.ddc) };
    if (typeof o.error === 'string' && o.error) r.error = o.error.slice(0, 300);
    return r;
  } catch (e) { return null; }
}

function readJson(file) {
  try { const o = JSON.parse(fs.readFileSync(file, 'utf8')); return o && typeof o === 'object' ? o : null; } catch (e) { return null; }
}
function writeJson(file, obj, log) {
  try { fs.writeFileSync(file, JSON.stringify(obj)); } catch (e) { if (log) log.warn('brightness: could not save', path.basename(file), e.message); }
}

// ---- The controller.
function createBrightness(opts) {
  const run = opts.run || defaultRun;
  const log = opts.log || { info() {}, warn() {}, error() {} };
  const now = opts.now || Date.now;
  const timeoutMs = opts.timeoutMs || RUN_TIMEOUT_MS;
  const stateFile = path.join(opts.dataDir, STATE_FILE);
  const probeFile = path.join(opts.dataDir, PROBE_FILE);
  // managed: this app has changed the brightness at least once, so 100 % must be set back as well.
  let managed = !!(readJson(stateFile) || {}).managed;
  let settings = null;
  let held = false;
  let last = null;          // { level, ok, at } of the last run
  let target = null;        // the level last asked for (running, waiting or done)
  let wanted = null;        // the newest level waiting for a run
  let busy = null;          // the running loop's promise
  let timer = null;
  let everOk = false;       // a level was set since this app started

  async function apply(level) {
    if (!managed) { managed = true; writeJson(stateFile, { managed: true }, log); }
    let res;
    try { res = await run('powershell.exe', psArgs(level), { timeout: timeoutMs }); } catch (e) { res = { ok: false, err: e.message }; }
    const r = res && res.ok ? parseResult(res.out) : null;
    last = { level, ok: !!r && (r.wmi + r.ddc) > 0, at: now() };
    if (last.ok) everOk = true;
    // The probe found a screen but none takes a level (e.g. a WMI class whose set call fails): forget the
    // probe so the page goes back to its darkening layer instead of never dimming. Only when nothing was
    // set since the start, so a screen that is merely asleep for a while does not switch modes.
    if (r && !last.ok && !everOk && canControl()) {
      writeJson(probeFile, { wmi: 0, ddc: 0, at: new Date(now()).toISOString(), set_failed: true }, log);
      if (typeof opts.onLost === 'function') { try { opts.onLost(); } catch (e) { /* ignore */ } }
    }
    if (!r) log.warn('brightness: could not set', level + '%:', (res && res.err) || 'no result');
    else if (!last.ok) log.warn('brightness: no screen took', level + '%', r.error || '');
    else log.info('brightness', level + '%', '(wmi ' + r.wmi + ', ddc/ci ' + r.ddc + ')' + (r.error ? ' ' + r.error : ''));
  }

  // One PowerShell at a time; levels asked for meanwhile collapse to the newest.
  function schedule(level) {
    wanted = level;
    if (busy) return busy;
    busy = (async () => {
      while (wanted !== null) {
        const l = wanted;
        wanted = null;
        await apply(l);
      }
    })().catch((e) => log.error('brightness', e.message)).finally(() => { busy = null; });
    return busy;
  }

  function canControl() {
    const p = readJson(probeFile);
    return !!p && ((Number(p.wmi) || 0) + (Number(p.ddc) || 0)) > 0;
  }

  function current() { return held ? 100 : levelAt(settings, now()); }

  // Runs PowerShell only when the wanted level differs from the last one (or a failed one is due again).
  function tick() {
    const level = current();
    if (!managed && level === 100) return Promise.resolve(); // never asked to dim: the user's own setting stays
    if (level === target) {
      const due = !busy && last && last.level === level && !last.ok && now() - last.at >= RETRY_MS;
      if (!due) return busy || Promise.resolve();
    }
    target = level;
    return schedule(level);
  }

  return {
    // The manifest's `settings` object (or null).
    update(s) { settings = s && typeof s === 'object' ? s : null; return tick(); },
    // true while an emergency message is on screen: full brightness until released.
    hold(full) { held = !!full; return tick(); },
    tick,
    start() {
      if (!timer) { timer = setInterval(tick, TICK_MS); if (timer.unref) timer.unref(); }
    },
    stop() { clearInterval(timer); timer = null; },
    idle() { return busy || Promise.resolve(); },
    level: current,
    isManaged: () => managed,
    // The cached probe: true when the last probe found at least one screen this PC can dim.
    canControl,
    // The page went back to its darkening layer: drop the settings and the hold, so the screen's own
    // brightness goes back to 100 (if it can) and the picture is never dimmed twice.
    release() { settings = null; held = false; return tick(); },
    // Counts the screens that can be controlled (changes nothing) and caches the result. {wmi, ddc} or null.
    async probe() {
      let res;
      try { res = await run('powershell.exe', psArgs(null), { timeout: timeoutMs }); } catch (e) { res = { ok: false, err: e.message }; }
      const r = res && res.ok ? parseResult(res.out) : null;
      if (!r) { log.warn('brightness probe failed', (res && res.err) || 'no result'); return null; }
      writeJson(probeFile, { wmi: r.wmi, ddc: r.ddc, at: new Date(now()).toISOString() }, log);
      log.info('brightness probe: wmi', r.wmi, 'ddc/ci', r.ddc);
      return r;
    },
  };
}

module.exports = { levelAt, minuteOfDay, script, psArgs, parseResult, validLevel, createBrightness, STATE_FILE, PROBE_FILE };
