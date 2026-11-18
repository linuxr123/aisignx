'use strict';
// The watchdog's files and its current-user Scheduled Task (schtasks, no elevation).
// Everything tolerates failure: the player never depends on the watchdog being installed.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFile } = require('child_process');

const TASK_NAME = 'AiSignXWatchdog';

function agentDir() {
  const base = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
  return path.join(base, 'AiSignX', 'agent');
}

function writeJsonAtomic(file, obj) {
  const tmp = file + '.tmp';
  const fd = fs.openSync(tmp, 'w');
  try { fs.writeFileSync(fd, JSON.stringify(obj)); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }   // on disk BEFORE the rename: a power cut never leaves an empty file
  fs.renameSync(tmp, file);
}
function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^﻿/, '')); } catch (e) { return null; }
}

function xmlEscape(s) {
  return String(s).replace(/[<>&"']/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' }[c]));
}

// Task Scheduler XML: every minute, current user, only while logged on, no battery limits,
// one instance at a time, hidden, killed after 5 minutes. conhost --headless avoids a console flash.
function buildTaskXml(opts) {
  const ps = 'powershell.exe';
  const args = '-NoProfile -ExecutionPolicy Bypass -File "' + opts.script + '" -Dir "' + opts.dir + '"';
  const user = (process.env.USERDOMAIN ? process.env.USERDOMAIN + '\\' : '') + (process.env.USERNAME || '');
  return '<?xml version="1.0" encoding="UTF-16"?>\n' +
    '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n' +
    '  <RegistrationInfo><Description>AiSignX Player watchdog (restarts the player when it hangs or exits)</Description></RegistrationInfo>\n' +
    '  <Triggers><TimeTrigger><Repetition><Interval>PT1M</Interval></Repetition>' +
    '<StartBoundary>2020-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger></Triggers>\n' +
    '  <Principals><Principal id="Author">' + (user ? '<UserId>' + xmlEscape(user) + '</UserId>' : '') +
    '<LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n' +
    '  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>' +
    '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>' +
    '<StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT5M</ExecutionTimeLimit><Hidden>true</Hidden></Settings>\n' +
    '  <Actions Context="Author"><Exec><Command>' + xmlEscape(opts.headless ? 'conhost.exe' : ps) + '</Command>' +
    '<Arguments>' + xmlEscape((opts.headless ? '--headless ' + ps + ' ' : '') + args) + '</Arguments></Exec></Actions>\n' +
    '</Task>\n';
}

function run(cmd, args) {
  return new Promise((resolve) => {
    execFile(cmd, args, { windowsHide: true, timeout: 20000 }, (err, stdout, stderr) => {
      resolve({ ok: !err, code: err ? err.code : 0, out: String(stdout || '') + String(stderr || '') });
    });
  });
}

// Copies the script next to the data (a stable path, outside the asar and the install folder),
// writes the flag file and registers the task. -> {ok, message}
async function register(o) {
  const dir = o.dir;
  try {
    fs.mkdirSync(dir, { recursive: true });
    const script = path.join(dir, 'watchdog.ps1');
    fs.copyFileSync(o.scriptSource, script);
    writeJsonAtomic(path.join(dir, 'flag.json'), { enabled: true, exe: o.exe, args: o.args || [] });
    const xmlFile = path.join(dir, 'task.xml');
    fs.writeFileSync(xmlFile, Buffer.concat([Buffer.from([0xff, 0xfe]),
      Buffer.from(buildTaskXml({ script, dir, headless: o.headless !== false }), 'utf16le')]));
    const r = await run('schtasks.exe', ['/Create', '/TN', o.taskName || TASK_NAME, '/XML', xmlFile, '/F']);
    return r.ok ? { ok: true, message: 'registered' } : { ok: false, message: 'schtasks failed: ' + r.out.trim().slice(0, 200) };
  } catch (e) {
    return { ok: false, message: 'register failed: ' + e.message };
  }
}

// Disables the watchdog: flag off first (the script obeys it even if the delete fails), then delete the task.
async function unregister(o) {
  const dir = o.dir;
  let msg = 'removed';
  try {
    const flagFile = path.join(dir, 'flag.json');
    const flag = readJson(flagFile);
    if (flag) writeJsonAtomic(flagFile, Object.assign({}, flag, { enabled: false }));
  } catch (e) { msg = 'flag not updated: ' + e.message; }
  const q = await run('schtasks.exe', ['/Query', '/TN', o.taskName || TASK_NAME]);
  if (!q.ok) return { ok: true, message: msg + ' (no task)' };
  const r = await run('schtasks.exe', ['/Delete', '/TN', o.taskName || TASK_NAME, '/F']);
  return r.ok ? { ok: true, message: msg } : { ok: false, message: 'schtasks delete failed: ' + r.out.trim().slice(0, 200) };
}

module.exports = { TASK_NAME, agentDir, buildTaskXml, register, unregister, writeJsonAtomic, readJson };
