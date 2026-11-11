"""Key/value organization settings stored in the database."""
from ..extensions import db

# Known settings and their defaults.
DEFAULTS = {
    "org_name": "My Organization",
    "timezone": "UTC",
}


class Setting(db.Model):
    __tablename__ = "settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=True)

    @staticmethod
    def get(key: str, default=None):
        row = db.session.get(Setting, key)
        if row is not None and row.value is not None:
            return row.value
        return default if default is not None else DEFAULTS.get(key)

    @staticmethod
    def set(key: str, value) -> None:
        row = db.session.get(Setting, key)
        if row is None:
            row = Setting(key=key)
            db.session.add(row)
        row.value = None if value is None else str(value)
