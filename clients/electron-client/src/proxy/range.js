'use strict';
// HTTP Range support for cached media files (single ranges only).
const fs = require('fs');

// -> null (no usable Range: send the whole file), {start, end} or {status: 416}.
// Malformed and multi-range headers are ignored, as RFC 9110 allows.
function parseRange(header, size) {
  if (typeof header !== 'string' || !header) return null;
  const m = /^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$/i.exec(header);
  if (!m || (m[1] === '' && m[2] === '')) return null;
  let start;
  let end;
  if (m[1] === '') {
    const n = Number(m[2]);
    if (n === 0) return { status: 416 };
    start = Math.max(0, size - n);
    end = size - 1;
  } else {
    start = Number(m[1]);
    end = m[2] === '' ? size - 1 : Math.min(Number(m[2]), size - 1);
    if (m[2] !== '' && Number(m[2]) < start) return null;
  }
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end)) return null;
  if (size === 0 || start >= size) return { status: 416 };
  return { start, end };
}

// Writes 200, 206 or 416 for `file` (size bytes) and streams the body.
function sendFile(req, res, file, size, headers) {
  const range = parseRange(req.headers.range, size);
  const base = Object.assign({ 'Accept-Ranges': 'bytes' }, headers);
  if (range && range.status === 416) {
    res.writeHead(416, Object.assign(base, { 'Content-Range': 'bytes */' + size, 'Content-Length': 0 }));
    res.end();
    return;
  }
  const start = range ? range.start : 0;
  const end = range ? range.end : size - 1;
  const h = Object.assign(base, { 'Content-Length': size === 0 ? 0 : end - start + 1 });
  if (range) h['Content-Range'] = 'bytes ' + start + '-' + end + '/' + size;
  res.writeHead(range ? 206 : 200, h);
  if (req.method === 'HEAD' || size === 0) {
    res.end();
    return;
  }
  const stream = fs.createReadStream(file, { start, end });
  stream.on('error', () => res.destroy());
  res.on('close', () => stream.destroy());
  stream.pipe(res);
}

module.exports = { parseRange, sendFile };
