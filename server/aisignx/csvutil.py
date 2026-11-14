"""CSV export helpers: formula-safe cells and streamed responses."""
import csv
import io
import re
from collections.abc import Iterable
from datetime import datetime

from flask import Response, stream_with_context

from .timeutil import iso_z

# A spreadsheet treats a cell starting with one of these as a formula.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_FLUSH_EVERY = 200
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_cell(value) -> str:
    """The value as text. Strings that start like a formula get a leading quote.

    Numbers are left alone, so a negative number stays a number.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return iso_z(value) or ""
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text.startswith(_FORMULA_START):
        return "'" + text
    return text


def stream_csv(filename: str, header: Iterable, rows: Iterable[Iterable]) -> Response:
    """A streamed text/csv download (UTF-8 with a BOM, so Excel reads it correctly).

    `rows` may be a generator; it is consumed while the response is sent, inside the
    request context.
    """
    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        buf.write("﻿")
        writer.writerow([safe_cell(c) for c in header])
        for i, row in enumerate(rows, 1):
            writer.writerow([safe_cell(c) for c in row])
            if i % _FLUSH_EVERY == 0:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    name = _SAFE_NAME.sub("_", filename).strip("._") or "export.csv"
    resp = Response(stream_with_context(generate()), content_type="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
