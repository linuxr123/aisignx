'use strict';
// Windows programs the agent starts are named by full path (SystemRoot\System32), never by bare name: a bare name is
// searched in the current directory first. Anything that is not one of our fixed names is returned unchanged.
const path = require('path');

const KNOWN = {
  'powershell.exe': 'WindowsPowerShell\\v1.0\\powershell.exe',
  'icacls.exe': 'icacls.exe', 'shutdown.exe': 'shutdown.exe', 'taskkill.exe': 'taskkill.exe', 'schtasks.exe': 'schtasks.exe', 'reg.exe': 'reg.exe',
};

function resolve(name, env) {
  const rel = KNOWN[String(name).toLowerCase()];
  if (!rel) return name;
  const root = (env && env.SystemRoot) || 'C:\\Windows';
  return path.win32.join(root, 'System32', rel);
}

module.exports = { resolve };
