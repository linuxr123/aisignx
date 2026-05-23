"""
End-to-end smoke tests -- run with: python tests_smoke.py
Cleans up after itself (no lasting DB changes).
"""
import json
from app import app, db
from models import User, PendingDisplay, Display
from flask_login import FlaskLoginClient

app.test_client_class = FlaskLoginClient
errors = []

def ok(label):
    print(f"OK   {label}")

def fail(label, detail=""):
    msg = f"FAIL {label}" + (f" -- {detail}" if detail else "")
    errors.append(msg)
    print(msg)

def check(cond, label, detail=""):
    if cond:
        ok(label)
    else:
        fail(label, detail)

# Setup
with app.app_context():
    admin = User.query.filter_by(username="admin").first()
    if not admin:
        fail("SETUP", "No admin user found")
        raise SystemExit(1)
    user_id = admin.id
    PendingDisplay.query.filter(PendingDisplay.device_id.like("test-%")).delete()
    Display.query.filter(Display.device_id.like("test-%")).delete()
    db.session.commit()

# Admin pages
with app.app_context():
    admin = db.session.get(User, user_id)
    with app.test_client(user=admin) as c:
        for path in ["/displays", "/media", "/playlists", "/schedules",
                     "/groups", "/downloads", "/dashboard", "/settings/api", "/users"]:
            r = c.get(path)
            check(r.status_code == 200, f"GET {path}", f"status={r.status_code}")

# Public pages + registration
with app.app_context():
    with app.test_client() as c:
        for path in ["/request-access", "/api/version"]:
            r = c.get(path)
            check(r.status_code == 200, f"GET {path}", f"status={r.status_code}")

        r = c.post("/api/register/browser",
                   content_type="application/json",
                   data=json.dumps({"device_id": "test-browser-1",
                                    "friendly_name": "Test Browser",
                                    "resolution": "1920x1080"}))
        d = json.loads(r.data)
        check(d.get("status") == "pending", "POST /api/register/browser", str(d))

        r = c.get("/api/register/status/test-browser-1")
        d = json.loads(r.data)
        check(d.get("status") == "pending", "GET /api/register/status (pending)", str(d))

        r = c.post("/api/register",
                   content_type="application/json",
                   data=json.dumps({"device_id": "test-native-1",
                                    "friendly_name": "Test Native",
                                    "os": "win32 10", "app_version": "1.0.0",
                                    "resolution": "1920x1080"}))
        d = json.loads(r.data)
        check(d.get("status") == "pending", "POST /api/register", str(d))

# Admin approve / decline
with app.app_context():
    admin = db.session.get(User, user_id)
    with app.test_client(user=admin) as c:
        r = c.get("/api/register/pending")
        d = json.loads(r.data)
        pending = d.get("pending", [])
        check(len(pending) >= 2, f"GET /api/register/pending ({len(pending)} items)")
        ids = {p["device_id"]: p["id"] for p in pending}

        r = c.post(f"/api/register/{ids['test-browser-1']}/approve",
                   content_type="application/json",
                   data=json.dumps({"name": "Test Browser Display", "location": "Test"}))
        d = json.loads(r.data)
        check(d.get("status") == "success" and "token" in d,
              "POST /api/register/approve", str(d))

        r = c.post(f"/api/register/{ids['test-native-1']}/decline",
                   content_type="application/json", data="{}")
        d = json.loads(r.data)
        check(d.get("status") == "success", "POST /api/register/decline", str(d))

# Poll after decision
with app.app_context():
    with app.test_client() as c:
        r = c.get("/api/register/status/test-browser-1")
        d = json.loads(r.data)
        check(d.get("status") == "approved" and "token" in d,
              "GET /api/register/status after approve", str(d))

        r = c.get("/api/register/status/test-native-1")
        d = json.loads(r.data)
        check(d.get("status") == "declined",
              "GET /api/register/status after decline", str(d))

# Version manifest
with app.app_context():
    with app.test_client() as c:
        r = c.get("/api/version")
        d = json.loads(r.data)
        check("clients" in d and "android" in d.get("clients", {}),
              "GET /api/version structure", str(d))

# Clean up
with app.app_context():
    PendingDisplay.query.filter(PendingDisplay.device_id.like("test-%")).delete()
    Display.query.filter(Display.device_id.like("test-%")).delete()
    db.session.commit()

print()
if errors:
    print("=" * 40)
    print(f"  {len(errors)} FAILURE(S):")
    for e in errors:
        print(f"  {e}")
    print("=" * 40)
else:
    print("All checks passed -- ready to test.")
