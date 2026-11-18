'use strict';
// Shell mode: the launcher (shell-launcher.ps1) respawns the player the moment it exits. During an update the agent
// writes the launcher's "updating" marker first, so the launcher waits for the installer instead of racing it (and
// does not drop to the Explorer desktop if the file swap fails for a moment).
// The marker lives in the kiosk user's own profile, which that user can write: the agent (SYSTEM) therefore only
// writes it when every folder on the way is a plain directory (no junction or symbolic link), and never follows a
// link at the file itself. A race between the check and the write remains possible; the worst case is a file named
// "updating" with a number inside, created in a folder the user could already write to.
const fs = require('fs');
const path = require('path');

const REL = ['AppData', 'Local', 'AiSignX', 'agent'];

// profileDir: the kiosk profile folder (from the registry ProfileList). fsx: injectable fs. -> the marker path or null.
function markerPath(profileDir, fsx) {
  const f = fsx || fs;
  if (typeof profileDir !== 'string' || !/^[A-Za-z]:\\/.test(profileDir)) return null;
  let cur = profileDir;
  try {
    for (const part of [''].concat(REL)) {
      if (part) cur = path.win32.join(cur, part);
      const s = f.lstatSync(cur);
      if (s.isSymbolicLink() || !s.isDirectory()) return null;
    }
  } catch (e) { return null; }                  // the launcher folder does not exist: not shell mode, nothing to do
  return path.win32.join(cur, 'updating');
}

function write(profileDir, nowMs, fsx) {
  const f = fsx || fs;
  const file = markerPath(profileDir, f);
  if (!file) return false;
  try {
    try { f.rmSync(file, { force: true }); } catch (e) { /* ignore */ }
    const fd = f.openSync(file, 'wx');           // 'wx': refuses to follow anything that appeared in the meantime
    try { f.writeSync(fd, String(nowMs)); } finally { f.closeSync(fd); }
    return true;
  } catch (e) { return false; }
}

function clear(profileDir, fsx) {
  const f = fsx || fs;
  const file = markerPath(profileDir, f);
  if (!file) return false;
  try { f.rmSync(file, { force: true }); return true; } catch (e) { return false; }
}

// Output of `reg query HKLM\...\ProfileList\<sid> /v ProfileImagePath` -> the folder or ''.
function parseProfileQuery(text) {
  const m = /ProfileImagePath\s+REG_(?:EXPAND_)?SZ\s+(.+?)\s*$/m.exec(String(text));
  return m && /^[A-Za-z]:\\[^"<>|*?\r\n]+$/.test(m[1]) ? m[1] : '';
}

module.exports = { markerPath, write, clear, parseProfileQuery, REL };
