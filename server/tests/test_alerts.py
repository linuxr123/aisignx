"""Alerts: the sweep rules, the mailer and the alerts pages."""
import smtplib
from collections import namedtuple
from datetime import timedelta

import pytest

from aisignx import create_app
from aisignx.alerts import mailer, rules, service
from aisignx.extensions import db
from aisignx.models import Alert, AuditLog, Display, Setting
from aisignx.timeutil import utcnow

from .conftest import login, make_user, run_job

Usage = namedtuple("Usage", "total used free")
GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _no_grace(monkeypatch):
    """Tests run long after "start-up" unless they say otherwise."""
    monkeypatch.setattr(rules, "uptime_s", lambda: 10 ** 6)
    mailer._last_attempt.clear()


def _display(app, name="Lobby", seen_ago=None, paired=True, muted=False) -> int:
    with app.app_context():
        d = Display(name=name, token_hash=(f"hash-{name}" if paired else None),
                    last_seen_at=(utcnow() - seen_ago) if seen_ago is not None else None,
                    alerts_muted=muted)
        db.session.add(d)
        db.session.commit()
        return d.id


def _alerts(app, kind=None, open_only=False):
    with app.app_context():
        q = Alert.query
        if kind:
            q = q.filter_by(kind=kind)
        if open_only:
            q = q.filter(Alert.resolved_at.is_(None))
        return [(a.key, a.severity, a.resolved_at is None) for a in q.order_by(Alert.id)]


def _sweep(app, **kw):
    with app.app_context():
        assert rules.sweep(**kw) == []


def _set(app, **values):
    with app.app_context():
        for key, value in values.items():
            Setting.set(key.replace("__", "."), value)
        db.session.commit()


# --- service ------------------------------------------------------------------------------

def test_open_alert_is_idempotent_and_updates(app):
    with app.app_context():
        a = service.open_alert("disk_low", "disk:x", "warning", "Low")
        b = service.open_alert("disk_low", "disk:x", "critical", "Very low", "d")
        assert a.id == b.id and b.severity == "critical" and b.title == "Very low"
        assert service.open_count() == 1
        assert service.resolve("disk_low", "disk:x") is True
        assert service.resolve("disk_low", "disk:x") is False
        c = service.open_alert("disk_low", "disk:x", "warning", "Low")
        assert c.id != a.id


def test_purge_job_deletes_old_resolved(app):
    with app.app_context():
        old = utcnow() - timedelta(days=100)
        db.session.add_all([
            Alert(kind="disk_low", key="a", title="old", resolved_at=old),
            Alert(kind="disk_low", key="b", title="new", resolved_at=utcnow()),
            Alert(kind="disk_low", key="c", title="open", opened_at=old),
        ])
        db.session.commit()
    assert "deleted 1" in run_job(app, "alerts.purge")
    with app.app_context():
        assert sorted(a.title for a in Alert.query) == ["new", "open"]


# --- display_offline ----------------------------------------------------------------------

def test_offline_opens_once_and_resolves_when_seen(app):
    did = _display(app, seen_ago=timedelta(minutes=30))
    _display(app, "Fresh", seen_ago=timedelta(minutes=1))
    _display(app, "Never")  # paired but never seen
    _display(app, "Unpaired", seen_ago=timedelta(hours=1), paired=False)
    _sweep(app)
    _sweep(app)
    assert _alerts(app) == [(f"display:{did}", "warning", True)]
    with app.app_context():
        db.session.get(Display, did).last_seen_at = utcnow()
        db.session.commit()
    _sweep(app)
    assert _alerts(app) == [(f"display:{did}", "warning", False)]


def test_offline_critical_after_a_day_and_resolves_on_delete(app):
    did = _display(app, seen_ago=timedelta(hours=25))
    _sweep(app)
    assert _alerts(app) == [(f"display:{did}", "critical", True)]
    with app.app_context():
        db.session.delete(db.session.get(Display, did))
        db.session.commit()
    _sweep(app)
    assert _alerts(app, open_only=True) == []


def test_startup_grace_opens_nothing_but_still_resolves(app, monkeypatch):
    did = _display(app, seen_ago=timedelta(minutes=30))
    monkeypatch.setattr(rules, "uptime_s", lambda: 60)  # 1 min < 10 + 2 min
    _sweep(app)
    assert _alerts(app) == []
    monkeypatch.setattr(rules, "uptime_s", lambda: 13 * 60)
    _sweep(app)
    assert len(_alerts(app, open_only=True)) == 1
    monkeypatch.setattr(rules, "uptime_s", lambda: 60)
    with app.app_context():
        db.session.get(Display, did).last_seen_at = utcnow()
        db.session.commit()
    _sweep(app)
    assert _alerts(app, open_only=True) == []


def test_muted_display_is_skipped_and_mute_resolves(app):
    _display(app, "Off", seen_ago=timedelta(hours=2), muted=True)
    did = _display(app, seen_ago=timedelta(hours=2))
    _sweep(app)
    assert _alerts(app, open_only=True) == [(f"display:{did}", "warning", True)]
    with app.app_context():
        db.session.get(Display, did).alerts_muted = True
        db.session.commit()
    _sweep(app)
    assert _alerts(app, open_only=True) == []


# --- disk_low -----------------------------------------------------------------------------

def test_disk_thresholds_and_hysteresis(app, monkeypatch):
    free = {"value": 100 * GB}
    monkeypatch.setattr(rules.shutil, "disk_usage",
                        lambda path: Usage(1000 * GB, 1000 * GB - free["value"], free["value"]))
    monkeypatch.setattr(rules, "disk_locations", lambda: [("uploads", "x")])
    _set(app, alerts__disk_min_free_gb=10, alerts__disk_min_free_pct=5)
    _sweep(app)
    assert _alerts(app) == []
    free["value"] = 40 * GB  # 4 % < 5 %
    _sweep(app)
    assert _alerts(app) == [("disk:uploads", "warning", True)]
    free["value"] = 20 * GB  # 2 % < 2.5 %: critical
    _sweep(app)
    assert _alerts(app) == [("disk:uploads", "critical", True)]
    free["value"] = 55 * GB  # 5.5 %: above the threshold but below 1.2x (6 %)
    _sweep(app)
    assert _alerts(app, open_only=True) == [("disk:uploads", "critical", True)]
    free["value"] = 61 * GB
    _sweep(app)
    assert _alerts(app, open_only=True) == []


def test_disk_gb_threshold(app, monkeypatch):
    monkeypatch.setattr(rules.shutil, "disk_usage", lambda path: Usage(20 * GB, 19 * GB, GB))
    monkeypatch.setattr(rules, "disk_locations", lambda: [("database", "x")])
    _set(app, alerts__disk_min_free_gb=2, alerts__disk_min_free_pct=0)
    _sweep(app)
    assert _alerts(app) == [("disk:database", "warning", True)]


def test_disk_locations_are_deduplicated(app):
    with app.app_context():
        locations = rules.disk_locations()
    labels = [label for label, _ in locations]
    assert labels[0] == "uploads" and len(labels) == 1  # all under one tmp_path


# --- login_burst --------------------------------------------------------------------------

def _failed_logins(app, n, ago=timedelta(minutes=1), ip="10.0.0.9", username="admin"):
    with app.app_context():
        for i in range(n):
            db.session.add(AuditLog(ts=utcnow() - ago, action="auth.login_failed", via="web",
                                    ip=ip, detail=f'{{"username":"{username}"}}'))
        db.session.commit()


def test_login_burst(app):
    _set(app, alerts__login_burst=5)
    _failed_logins(app, 4)
    _sweep(app)
    assert _alerts(app) == []
    _failed_logins(app, 2, ip="10.0.0.7", username="bob")
    _sweep(app)
    assert _alerts(app) == [("login:all", "warning", True)]
    with app.app_context():
        detail = Alert.query.one().detail
    assert "10.0.0.9 (4)" in detail and "bob (2)" in detail
    with app.app_context():  # failures age out of the window but are recent: kept open
        for row in AuditLog.query:
            row.ts = utcnow() - timedelta(minutes=20)
        db.session.commit()
    _sweep(app)
    assert _alerts(app, open_only=True) == [("login:all", "warning", True)]
    with app.app_context():
        for row in AuditLog.query:
            row.ts = utcnow() - timedelta(minutes=31)
        db.session.commit()
    _sweep(app)
    assert _alerts(app, open_only=True) == []


# --- backups and jobs ---------------------------------------------------------------------

def test_backup_failed_and_resolved_by_success(app):
    now = utcnow()
    with app.app_context():
        Setting.set_datetime("backup.last_ok_at", now - timedelta(hours=10))
        Setting.set_datetime("backup.last_error_at", now - timedelta(hours=1))
        Setting.set("backup.last_error", "disk full")
        db.session.commit()
    _sweep(app)
    assert _alerts(app, "backup_failed") == [("backup", "critical", True)]
    with app.app_context():
        Setting.set_datetime("backup.last_ok_at", now)
        db.session.commit()
    _sweep(app)
    assert _alerts(app, "backup_failed", open_only=True) == []


def test_backup_stale(app):
    with app.app_context():
        Setting.set_datetime("backup.last_ok_at", utcnow() - timedelta(hours=50))
        db.session.commit()
    _sweep(app)
    assert _alerts(app, "backup_stale") == [("backup", "warning", True)]
    _set(app, backup__enabled=0)
    _sweep(app)
    assert _alerts(app, "backup_stale", open_only=True) == []
    _set(app, backup__enabled=1)
    with app.app_context():
        Setting.set_datetime("backup.last_ok_at", utcnow())
        db.session.commit()
    _sweep(app)
    assert _alerts(app, "backup_stale", open_only=True) == []


def test_job_failed(app):
    _set(app, **{"job__pop.rollup__fail_count": 3, "job__pop.rollup__last_error": "boom"})
    _sweep(app)
    assert _alerts(app, "job_failed") == [("job:pop.rollup", "warning", True)]
    _set(app, **{"job__pop.rollup__fail_count": 1})
    _sweep(app)
    assert _alerts(app, "job_failed", open_only=True) == [("job:pop.rollup", "warning", True)]
    _set(app, **{"job__pop.rollup__fail_count": 0})
    _sweep(app)
    assert _alerts(app, "job_failed", open_only=True) == []


def test_sweep_job_runs_through_jobs(app):
    assert "open" in run_job(app, "alerts.sweep")


# --- mailer -------------------------------------------------------------------------------

class FakeSMTP:
    sent: list = []
    logins: list = []
    fail = False
    instances = 0

    def __init__(self, host, port, timeout=None, **kw):
        FakeSMTP.instances += 1
        self.host, self.port = host, port

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        FakeSMTP.logins.append((user, password))

    def send_message(self, msg, from_addr=None, to_addrs=None):
        if FakeSMTP.fail:
            raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (550, b"no")})
        FakeSMTP.sent.append((msg, to_addrs))

    def quit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def smtp(app, monkeypatch):
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.fail, FakeSMTP.instances = [], [], False, 0
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    app.config["SMTP_PASSWORD"] = "pw"
    _set(app, smtp__host="mail.example.test", smtp__from="signage@example.test",
         smtp__username="signage", alerts__email_to="a@example.test, b@example.test",
         alerts__email_enabled=1, org_name="Acme")
    return FakeSMTP


def test_mail_one_message_per_recipient(app, smtp):
    with app.app_context():
        service.open_alert("disk_low", "disk:uploads", "warning", "Low disk space (uploads)")
    run_job(app, "alerts.mail")
    assert [to for _, to in smtp.sent] == [["a@example.test"], ["b@example.test"]]
    for msg, to in smtp.sent:
        assert msg["To"] == to[0] and "Cc" not in msg and "Bcc" not in msg
        assert msg["Subject"] == "[AiSignX] Acme: Low disk space (uploads)"
        assert msg["Date"] and msg["Message-ID"]
        other = "b@example.test" if to[0] == "a@example.test" else "a@example.test"
        assert other not in msg.as_string()
    assert smtp.logins == [("signage", "pw")]
    run_job(app, "alerts.mail")  # nothing new
    assert len(smtp.sent) == 2
    with app.app_context():
        service.resolve("disk_low", "disk:uploads")
    run_job(app, "alerts.mail")
    assert [m["Subject"] for m, _ in smtp.sent[2:]] == ["[AiSignX] Resolved: Low disk space (uploads)"] * 2


def test_mail_restart_sends_no_second_email(app, smtp, tmp_path, monkeypatch):
    did = _display(app, seen_ago=timedelta(hours=1))
    _sweep(app)
    run_job(app, "alerts.mail")
    assert len(smtp.sent) == 2
    # Same database, fresh app: the alert is still open and already mailed.
    app2 = create_app({**app.config, "TESTING": True})
    _sweep(app2)
    run_job(app2, "alerts.mail")
    assert len(smtp.sent) == 2
    assert _alerts(app2) == [(f"display:{did}", "warning", True)]


def test_mail_failure_retries_with_backoff(app, smtp, monkeypatch):
    with app.app_context():
        service.open_alert("disk_low", "disk:uploads", "warning", "Low")
    smtp.fail = True
    with pytest.raises(RuntimeError):
        run_job(app, "alerts.mail")
    with app.app_context():
        alert = Alert.query.one()
        assert alert.notified_at is None and alert.notify_attempts == 1
        assert "SMTPRecipientsRefused" in alert.last_notify_error
    smtp.fail = False
    run_job(app, "alerts.mail")  # still inside the 2-minute backoff
    assert smtp.sent == []
    clock = mailer.time.monotonic() + 121
    monkeypatch.setattr(mailer.time, "monotonic", lambda: clock)
    run_job(app, "alerts.mail")
    assert len(smtp.sent) == 2
    with app.app_context():
        alert = Alert.query.one()
        assert alert.notified_at is not None and alert.notify_attempts == 0
        assert alert.last_notify_error is None


def test_mail_skips_expired_and_unmailed_resolved(app, smtp):
    with app.app_context():
        old = utcnow() - timedelta(hours=30)
        db.session.add_all([
            Alert(kind="disk_low", key="old", title="Old", opened_at=old, last_seen_at=old),
            Alert(kind="disk_low", key="gone", title="Gone", resolved_at=utcnow()),
        ])
        db.session.commit()
    assert "skipped 2" in run_job(app, "alerts.mail")
    assert smtp.sent == []
    with app.app_context():
        errors = {a.key: (a.last_notify_error, a.notified_at is not None) for a in Alert.query}
    assert errors == {"old": ("expired", True), "gone": ("resolved before notice", True)}
    with app.app_context():
        service.resolve("disk_low", "old")
    run_job(app, "alerts.mail")
    assert smtp.sent == []  # an alert never mailed open is not mailed resolved


def test_mail_off_or_unconfigured(app, smtp):
    with app.app_context():
        service.open_alert("disk_low", "disk:uploads", "warning", "Low")
    _set(app, alerts__email_enabled=0)
    assert run_job(app, "alerts.mail") == "e-mail off"
    _set(app, alerts__email_enabled=1, alerts__email_to="")
    assert run_job(app, "alerts.mail") == "SMTP not configured"
    assert smtp.sent == []


def test_check_connection(app, smtp):
    with app.app_context():
        ok, detail = mailer.check_connection()
    assert ok and "mail.example.test" in detail and smtp.sent == []
    app.config["SMTP_PASSWORD"] = None
    with app.app_context():
        ok, detail = mailer.check_connection()
    assert not ok and "AISIGNX_SMTP_PASSWORD" in detail


def test_ssl_uses_smtp_ssl(app, smtp, monkeypatch):
    used = []

    class FakeSSL(FakeSMTP):
        def __init__(self, host, port, timeout=None, context=None):
            used.append(context is not None)
            super().__init__(host, port)

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSSL)
    _set(app, smtp__security="ssl")
    with app.app_context():
        assert mailer.check_connection()[0]
    assert used == [True]


# --- pages --------------------------------------------------------------------------------

def _as(client, app, role):
    make_user(app, role, role)
    login(client, role)
    return client


def test_viewer_sees_alerts_but_cannot_ack(app, client, configured):
    with app.app_context():
        alert = service.open_alert("disk_low", "disk:uploads", "warning", "Low disk space")
        alert_id = alert.id
    _as(client, app, "viewer")
    page = client.get("/alerts/")
    assert page.status_code == 200 and b"Low disk space" in page.data
    assert b"Acknowledge" not in page.data
    assert client.post(f"/alerts/{alert_id}/ack").status_code == 403
    assert client.get("/alerts/settings").status_code == 403
    assert client.get("/alerts/?tab=history").status_code == 200


def test_editor_acks_and_badge_updates(app, client, configured):
    with app.app_context():
        a = service.open_alert("disk_low", "disk:a", "warning", "A")
        service.open_alert("disk_low", "disk:b", "warning", "B")
        alert_id = a.id
    _as(client, app, "editor")
    assert 'data-alert-badge title="Open alerts">2</span>' in client.get("/alerts/").get_data(True)
    assert client.post(f"/alerts/{alert_id}/ack").status_code == 303
    html = client.get("/alerts/").get_data(True)
    assert 'data-alert-badge title="Open alerts">1</span>' in html
    assert "Acknowledged by editor" in html
    assert client.get("/alerts/settings").status_code == 403
    with app.app_context():
        row = AuditLog.query.filter_by(action="alert.ack").one()
        assert row.target_type == "alert" and row.target_id == alert_id


def test_mute_and_unmute(app, client, configured):
    did = _display(app, seen_ago=timedelta(hours=1))
    _sweep(app)
    _as(client, app, "editor")
    assert b"Mute display" in client.get("/alerts/").data
    assert client.post(f"/alerts/displays/{did}/mute").status_code == 303
    with app.app_context():
        assert db.session.get(Display, did).alerts_muted is True
        assert service.open_count() == 0
    html = client.get("/alerts/").get_data(True)
    assert "data-muted-displays" in html and "Lobby" in html
    _sweep(app)
    assert _alerts(app, open_only=True) == []
    assert client.post(f"/alerts/displays/{did}/unmute").status_code == 303
    with app.app_context():
        assert db.session.get(Display, did).alerts_muted is False
        actions = [r.action for r in AuditLog.query.order_by(AuditLog.id)
                   if r.action.startswith("alert.")]
    assert actions == ["alert.mute", "alert.unmute"]
    assert client.post("/alerts/displays/9999/mute").status_code == 404
    # `next` must stay on this site.
    ok = client.post(f"/alerts/displays/{did}/mute", data={"next": f"/displays/{did}"})
    assert ok.headers["Location"].endswith(f"/displays/{did}")
    for bad in ("//evil.test/x", "/\\evil.test/x", "https://evil.test/"):
        resp = client.post(f"/alerts/displays/{did}/unmute", data={"next": bad})
        assert resp.headers["Location"].endswith("/alerts/")


def test_settings_save_and_validate(admin_client, app):
    page = admin_client.get("/alerts/settings")
    assert page.status_code == 200 and b"not set" in page.data
    data = {"email_enabled": "y", "email_to": "ops@example.test; it@example.test",
            "smtp_host": "mail.example.test", "smtp_port": "465", "smtp_security": "ssl",
            "smtp_username": "", "smtp_from": "sign@example.test", "offline_minutes": "15",
            "disk_min_free_gb": "3", "disk_min_free_pct": "4", "login_burst": "0",
            "retention_days": "30"}
    assert admin_client.post("/alerts/settings", data=data).status_code == 302
    with app.app_context():
        assert Setting.get("alerts.email_to") == "ops@example.test, it@example.test"
        assert Setting.get_bool("alerts.email_enabled")
        assert Setting.get_int("alerts.offline_minutes") == 15
        assert Setting.get("smtp.security") == "ssl"
        row = AuditLog.query.filter_by(action="alerts.settings").one()
        assert "smtp.host" in row.detail_data["changed"]
    bad = dict(data, email_to="not-an-address", smtp_from="x")
    resp = admin_client.post("/alerts/settings", data=bad)
    assert resp.status_code == 200 and b"Not an e-mail address" in resp.data
    off = dict(data, smtp_host="")
    resp = admin_client.post("/alerts/settings", data=off)
    assert b"E-mail needs an SMTP server" in resp.data


def test_settings_shows_password_set_never_value(admin_client, app):
    app.config["SMTP_PASSWORD"] = "hunter2-secret"
    html = admin_client.get("/alerts/settings").get_data(True)
    assert "set in the environment" in html and "hunter2-secret" not in html


def test_send_test_email(admin_client, app, smtp):
    resp = admin_client.post("/alerts/settings/test", follow_redirects=True)
    assert b"Test e-mail sent" in resp.data and len(smtp.sent) == 2
    smtp.fail = True
    resp = admin_client.post("/alerts/settings/test", follow_redirects=True)
    assert b"The test e-mail failed" in resp.data
    with app.app_context():
        rows = AuditLog.query.filter_by(action="smtp.test").order_by(AuditLog.id).all()
        assert [r.detail_data["ok"] for r in rows] == [True, False]
