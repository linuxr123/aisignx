'use strict';
// update_app: stream the installer to a temp file while hashing it, and keep it only when the
// size and sha256 equal the SIGNED values. Pure Node (streams, fs, crypto).
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const V = require('./verify');

// Destroys a write stream and waits until its file handle is released (Windows cannot delete an open file).
function closed(stream) {
  return new Promise((resolve) => {
    if (stream.closed) return resolve();
    stream.once('close', resolve);
    stream.destroy();
  });
}

// body: async iterable of Buffers. want: {size, sha256}. -> {ok:true, size} | {ok:false, reason}.
// The file exists only on success; every failure removes it. Reads at most want.size + 1 bytes.
async function savePackage(body, file, want, declaredLength, opts) {
  if (declaredLength !== undefined && declaredLength !== null && Number(declaredLength) !== want.size) {
    return { ok: false, reason: 'size mismatch' };
  }
  const hash = crypto.createHash('sha256');
  let got = 0;
  let out = null;
  // Overall deadline: a slow-drip download must not hold the agent for ever.
  const deadlineMs = (opts && opts.deadlineMs) || 0;
  let timedOut = false;
  const timer = deadlineMs > 0 ? setTimeout(() => { timedOut = true; if (body && typeof body.destroy === 'function') body.destroy(new Error('deadline')); }, deadlineMs) : null;
  const drop = () => { try { fs.rmSync(file, { force: true }); } catch (e) { /* ignore */ } };
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    out = fs.createWriteStream(file, { flags: 'wx', mode: 0o600 });
    const done = new Promise((resolve, reject) => { out.on('close', resolve); out.on('error', reject); });
    done.catch(() => {});
    for await (const chunk of body) {
      got += chunk.length;
      if (got > want.size) { await closed(out); drop(); return { ok: false, reason: 'size mismatch' }; }
      hash.update(chunk);
      if (!out.write(chunk)) await new Promise((r) => out.once('drain', r));
    }
    out.end();
    await done;
    const check = V.checkPackage(want, got, hash.digest('hex'));
    if (!check.ok) { drop(); return check; }
    return { ok: true, size: got };
  } catch (e) {
    if (out) await closed(out);
    // Not removed when the 'wx' open itself failed: that file is not ours.
    if (!(e && e.code === 'EEXIST')) drop();
    return { ok: false, reason: timedOut ? 'download timed out' : 'download failed' };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// Hash and size of the file on disk, right before it is run (what was downloaded may have been
// swapped since). -> {ok:true} | {ok:false, reason}
async function verifyFile(file, want) {
  try {
    const hash = crypto.createHash('sha256');
    let got = 0;
    for await (const chunk of fs.createReadStream(file)) { got += chunk.length; hash.update(chunk); }
    return V.checkPackage(want, got, hash.digest('hex'));
  } catch (e) { return { ok: false, reason: 'file unreadable' }; }
}

// Installers left over from earlier updates (the running installer holds its own file, so a
// failed delete is simply tried again at the next start).
function cleanupUpdates(dir) {
  let names = [];
  try { names = fs.readdirSync(dir); } catch (e) { return 0; }
  let n = 0;
  for (const f of names) {
    try { fs.rmSync(path.join(dir, f), { force: true, recursive: true }); n++; } catch (e) { /* still in use */ }
  }
  return n;
}

module.exports = { savePackage, verifyFile, cleanupUpdates };
