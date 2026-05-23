from app import app, db
from flask_migrate import Migrate, init, migrate, upgrade, stamp
import os

migrate_obj = Migrate(app, db)

with app.app_context():
    # Initialize migration repository if it doesn't exist yet
    if not os.path.exists('migrations'):
        init()

    # Generate a new migration script detecting any model changes
    migrate()

    # Stamp the DB as 'head' first in case the revision history was reset,
    # then apply any pending migrations.
    try:
        upgrade()
    except Exception:
        # Revision not found — stamp head to re-sync history, then upgrade
        stamp(revision='head')
        upgrade()

print("Database schema updated successfully")
