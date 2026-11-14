"""Alert e-mail over SMTP (standard library only).

The SMTP settings live in Settings (smtp.*); the password comes from the environment
only (AISIGNX_SMTP_PASSWORD -> config SMTP_PASSWORD). Each recipient gets a separate
message, so no recipient sees the other addresses.

mail_job() sends one notice when an alert opens and one when it resolves, over one
connection per run. A notice counts as sent once any recipient accepted it (the other
addresses' errors are kept in last_notify_error), so one bad address never makes the
others get the same mail again. A notice that no recipient accepted is retried with
backoff (the backoff clock is per process: after a restart the next attempt comes at
once), unless every address was refused permanently (5xx). When the server cannot be
reached or drops the connection, the run stops and every remaining notice backs off.
"""
import logging
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

from flask import current_app

from ..extensions import db
from ..filters import localtime
from ..models import Alert, Setting
from ..timeutil import as_utc, utcnow

log = logging.getLogger(__name__)

TIMEOUT_S = 15
EXPIRE_AFTER = timedelta(hours=24)
MAX_PER_RUN = 50
SECURITIES = ("starttls", "ssl", "none")
SKIPPED = ("expired", "resolved before notice")
EMAIL_RE = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")

# (alert id, phase) -> time.monotonic() of the last failed attempt, for the backoff.
_last_attempt: dict[tuple[int, str], float] = {}


@dataclass
class SmtpSettings:
    host: str
    port: int
    security: str
    username: str
    password: str | None
    sender: str
    recipients: list[str]

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender and self.recipients)


def parse_recipients(text: str | None) -> list[str]:
    """Split a comma (or semicolon / newline) separated list; drop blanks and duplicates."""
    out = []
    for part in re.split(r"[,;\n]", text or ""):
        address = part.strip()
        if address and address.lower() not in (a.lower() for a in out):
            out.append(address)
    return out


def valid_address(address: str) -> bool:
    return bool(EMAIL_RE.match(address or ""))


def settings() -> SmtpSettings:
    security = (Setting.get("smtp.security") or "starttls").strip().lower()
    return SmtpSettings(
        host=(Setting.get("smtp.host") or "").strip(),
        port=Setting.get_int("smtp.port", lo=1, hi=65535),
        security=security if security in SECURITIES else "starttls",
        username=(Setting.get("smtp.username") or "").strip(),
        password=current_app.config.get("SMTP_PASSWORD") or None,
        sender=(Setting.get("smtp.from") or "").strip(),
        recipients=[a for a in parse_recipients(Setting.get("alerts.email_to"))
                    if valid_address(a)],
    )


def _connect(cfg: SmtpSettings) -> smtplib.SMTP:
    """An open, authenticated connection. The caller closes it."""
    if cfg.security == "ssl":
        conn = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=TIMEOUT_S,
                                context=ssl.create_default_context())
    else:
        conn = smtplib.SMTP(cfg.host, cfg.port, timeout=TIMEOUT_S)
    try:
        conn.ehlo()
        if cfg.security == "starttls":
            conn.starttls(context=ssl.create_default_context())
            conn.ehlo()
        if cfg.username:
            if cfg.password is None:
                raise smtplib.SMTPAuthenticationError(
                    535, "smtp.username is set but AISIGNX_SMTP_PASSWORD is not")
            conn.login(cfg.username, cfg.password)
    except Exception:
        _close(conn)
        raise
    return conn


def _close(conn) -> None:
    try:
        conn.quit()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def _message(cfg: SmtpSettings, to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = " ".join(subject.split())[:250]  # never a line break in a header
    msg["From"] = cfg.sender
    msg["To"] = to
    msg["Date"] = format_datetime(utcnow())
    domain = cfg.sender.rpartition("@")[2] or None
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.set_content(body)
    return msg


class ConnectionLost(Exception):
    """The connection failed while sending; `delivered` lists who got the message."""

    def __init__(self, cause: BaseException, delivered: list[str], failed: list):
        super().__init__(_describe(cause))
        self.cause, self.delivered, self.failed = cause, delivered, failed


def _connection_error(exc: BaseException) -> bool:
    """A failure of the connection itself (not of one message or one address)."""
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return True
    if isinstance(exc, smtplib.SMTPResponseException) and exc.smtp_code == 421:
        return True  # service not available, closing the channel
    # smtplib.SMTPException subclasses OSError; plain OSErrors are socket/TLS errors.
    return isinstance(exc, OSError) and not isinstance(exc, smtplib.SMTPException)


def _permanent(exc: BaseException) -> bool:
    """Every address in a recipient refusal got a 5xx: retrying cannot help."""
    refused = getattr(exc, "recipients", None)
    return (isinstance(exc, smtplib.SMTPRecipientsRefused) and bool(refused)
            and all(isinstance(v, tuple) and int(v[0]) >= 500 for v in refused.values()))


def _deliver(conn, cfg: SmtpSettings, subject: str, body: str,
             to: list[str]) -> tuple[list[str], list[tuple[str, Exception]]]:
    """Send one message per address. Returns (delivered, [(address, error)]); raises
    ConnectionLost on a connection-level failure."""
    delivered, failed = [], []
    for address in to:
        try:
            conn.send_message(_message(cfg, address, subject, body),
                              from_addr=cfg.sender, to_addrs=[address])
        except Exception as exc:
            if _connection_error(exc):
                raise ConnectionLost(exc, delivered, failed) from exc
            failed.append((address, exc))
        else:
            delivered.append(address)
    return delivered, failed


def _errors_text(failed) -> str:
    return "; ".join(f"{address}: {_describe(exc)}" for address, exc in failed)[:255]


def send(subject: str, body: str, to: list[str], cfg: SmtpSettings | None = None) -> None:
    """Send one message per recipient over one connection. Raises when any recipient
    failed (after trying all of them)."""
    cfg = cfg or settings()
    if not cfg.host or not cfg.sender:
        raise RuntimeError("SMTP is not configured")
    if not to:
        raise RuntimeError("no recipients")
    conn = _connect(cfg)
    try:
        try:
            _delivered, failed = _deliver(conn, cfg, subject, body, to)
        except ConnectionLost as lost:
            raise lost.cause
    finally:
        _close(conn)
    if failed:
        raise RuntimeError(_errors_text(failed))


def check_connection() -> tuple[bool, str]:
    """Connect, EHLO, STARTTLS and log in, without sending. For the System page."""
    cfg = settings()
    if not cfg.host:
        return False, "SMTP is not configured (no host)."
    try:
        conn = _connect(cfg)
    except Exception as exc:
        return False, _describe(exc)
    _close(conn)
    auth = f" and signed in as {cfg.username}" if cfg.username else ""
    return True, f"Connected to {cfg.host}:{cfg.port} ({cfg.security}){auth}."


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:255]


def _body(alert: Alert, resolved: bool) -> str:
    org = Setting.get("org_name") or "AiSignX"
    lines = [alert.title, ""]
    if alert.detail:
        lines += [alert.detail, ""]
    lines += [f"Organization: {org}", f"Kind: {alert.kind}", f"Severity: {alert.severity}",
              f"Opened: {localtime(alert.opened_at)}"]
    if resolved:
        lines.append(f"Resolved: {localtime(alert.resolved_at)}")
    lines += ["", "Sent by AiSignX. Open the Alerts page in the admin for details."]
    return "\n".join(lines)


def _subject(alert: Alert, resolved: bool) -> str:
    if resolved:
        return f"[AiSignX] Resolved: {alert.title}"
    return f"[AiSignX] {Setting.get('org_name') or 'AiSignX'}: {alert.title}"


def _backoff_over(alert: Alert, phase: str) -> bool:
    last = _last_attempt.get((alert.id, phase))
    if last is None or not alert.notify_attempts:
        return True
    wait_min = min(2 ** alert.notify_attempts, 60)
    return time.monotonic() - last >= wait_min * 60


def _pending() -> list[tuple[Alert, str]]:
    opened = Alert.query.filter(Alert.resolved_at.is_(None), Alert.notified_at.is_(None)) \
        .order_by(Alert.id).limit(MAX_PER_RUN).all()
    resolved = Alert.query.filter(Alert.resolved_at.is_not(None),
                                  Alert.resolve_notified_at.is_(None)) \
        .order_by(Alert.id).limit(MAX_PER_RUN).all()
    return [(a, "open") for a in opened] + [(a, "resolve") for a in resolved]


def _skip_reason(alert: Alert, phase: str, now) -> str | None:
    if phase == "resolve":
        if alert.notified_at is None:
            return "resolved before notice"  # the opening was never mailed
        if alert.last_notify_error in SKIPPED:
            return alert.last_notify_error  # neither was the closing worth a mail
        since = alert.resolved_at
    else:
        since = alert.opened_at
    if now - as_utc(since) > EXPIRE_AFTER:
        return "expired"
    return None


def _mark_done(alert: Alert, phase: str, now, error: str | None) -> None:
    if phase == "open" or error == "resolved before notice":
        alert.notified_at = alert.notified_at or now
    if phase == "resolve":
        alert.resolve_notified_at = now
    alert.notify_attempts = 0
    alert.last_notify_error = error
    _last_attempt.pop((alert.id, phase), None)


def _mark_failed(alert: Alert, phase: str, error: str) -> None:
    """Count a failed attempt; the notice is retried after the backoff."""
    log.warning("Alert mail for %s %s failed: %s", alert.kind, alert.key, error)
    alert.notify_attempts = (alert.notify_attempts or 0) + 1
    alert.last_notify_error = error[:255]
    _last_attempt[(alert.id, phase)] = time.monotonic()


def mail_job() -> str:
    """Job: mail new and resolved alerts to alerts.email_to."""
    if not Setting.get_bool("alerts.email_enabled"):
        return "e-mail off"
    cfg = settings()
    if not cfg.configured:
        return "SMTP not configured"
    now = utcnow()
    sent = skipped = failed = 0
    todo = []
    for alert, phase in _pending():
        reason = _skip_reason(alert, phase, now)
        if reason:
            _mark_done(alert, phase, now, reason)
            db.session.commit()
            skipped += 1
        elif _backoff_over(alert, phase):
            todo.append((alert, phase))
    if not todo:
        return f"sent 0, skipped {skipped}, failed 0"
    try:
        conn = _connect(cfg)
    except Exception as exc:
        for alert, phase in todo:
            _mark_failed(alert, phase, _describe(exc))
        db.session.commit()
        raise RuntimeError(f"cannot reach the SMTP server: {_describe(exc)}") from exc
    try:
        for index, (alert, phase) in enumerate(todo):
            resolved = phase == "resolve"
            try:
                delivered, errors = _deliver(conn, cfg, _subject(alert, resolved),
                                             _body(alert, resolved), cfg.recipients)
            except ConnectionLost as lost:
                if lost.delivered:  # partly sent: never send it to those addresses again
                    rest = [a for a in cfg.recipients if a not in lost.delivered]
                    _mark_done(alert, phase, utcnow(),
                               f"not sent to {', '.join(rest)}: {lost}"[:255])
                    sent += 1
                    remaining = todo[index + 1:]
                else:
                    remaining = todo[index:]
                for other, other_phase in remaining:  # stop the run; all of them back off
                    _mark_failed(other, other_phase, str(lost))
                    failed += 1
                db.session.commit()
                break
            if delivered:
                _mark_done(alert, phase, utcnow(), _errors_text(errors) or None)
                sent += 1
            elif all(_permanent(exc) for _, exc in errors):
                _mark_done(alert, phase, utcnow(), ("refused: " + _errors_text(errors))[:255])
                failed += 1
            else:
                _mark_failed(alert, phase, _errors_text(errors))
                failed += 1
            db.session.commit()
    finally:
        _close(conn)
    if failed and not sent:
        raise RuntimeError(f"{failed} alert e-mail(s) failed; last error kept on the alert")
    return f"sent {sent}, skipped {skipped}, failed {failed}"
