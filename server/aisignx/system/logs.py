"""Read the end of the log file without loading it whole.

Lines written by logging_setup look like `2026-11-12T10:00:00Z INFO    aisignx.x: msg`.
Lines without that header (tracebacks) belong to the record above them, so they are
kept or dropped together with it. When the current file is shorter than asked for, the
rotated files (aisignx.log.1, .2, ...) are read too.
"""
import logging
import re
from pathlib import Path

BLOCK = 64 * 1024
MAX_LINES = 1000
MAX_LINE_CHARS = 4000
MAX_SCAN_BYTES = 32 * 1024 * 1024  # never scan more than this per request
ROTATED = 5
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def level_of(line: str) -> str | None:
    """The level of a record's first line, or None for a continuation line."""
    parts = line.split(None, 2)
    if len(parts) >= 2 and _STAMP.match(parts[0]) and parts[1] in LEVELS:
        return parts[1]
    return None


def _threshold(min_level: str | None) -> int | None:
    if not min_level:
        return None
    value = logging.getLevelName(str(min_level).strip().upper())
    return value if isinstance(value, int) else None


def _reverse_lines(path: Path, budget: list[int]):
    """Yield the lines of path from last to first, reading 64 KB blocks from the end.

    budget[0] is the number of bytes still allowed to be read; it is decremented.
    """
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        rest = b""
        while pos > 0 and budget[0] > 0:
            size = min(BLOCK, pos, budget[0])
            pos -= size
            budget[0] -= size
            fh.seek(pos)
            chunk = fh.read(size) + rest
            pieces = chunk.split(b"\n")
            rest = pieces.pop(0)  # may continue in the previous block
            for raw in reversed(pieces):
                yield raw.rstrip(b"\r").decode("utf-8", "replace")
        if pos == 0 and rest:
            yield rest.rstrip(b"\r").decode("utf-8", "replace")


def log_files(path: Path) -> list[Path]:
    """The current log file and its rotated copies, newest first (existing ones only)."""
    path = Path(path)
    candidates = [path] + [path.with_name(f"{path.name}.{n}") for n in range(1, ROTATED + 1)]
    return [p for p in candidates if p.is_file()]


def tail(path, lines: int = 200, min_level: str | None = None) -> list[str]:
    """The last `lines` lines (at most 1000), oldest first.

    With min_level ("WARNING", ...) only records at or above that level are kept,
    with their traceback lines.
    """
    lines = max(1, min(int(lines), MAX_LINES))
    threshold = _threshold(min_level)
    records: list[list[str]] = []  # newest first, each in reading order
    pending: list[str] = []  # continuation lines (newest first) waiting for their header
    count = 0
    budget = [MAX_SCAN_BYTES]
    for file in log_files(Path(path)):
        reader = _reverse_lines(file, budget)
        try:
            for index, line in enumerate(reader):
                if index == 0 and line == "":
                    continue  # the newline at the end of the file
                line = line[:MAX_LINE_CHARS]
                level = level_of(line)
                if level is None:
                    pending.append(line)
                    continue
                record = [line] + pending[::-1]
                pending = []
                if threshold is None or logging.getLevelName(level) >= threshold:
                    records.append(record)
                    count += len(record)
                    if count >= lines:
                        break
        except OSError:
            continue
        finally:
            reader.close()  # release the file now (Windows locks open files)
        if count >= lines or budget[0] <= 0:
            break
    else:
        if pending and threshold is None:  # lines before the first header
            records.append(pending[::-1])
    out = [line for record in reversed(records) for line in record]
    return out[-lines:]
