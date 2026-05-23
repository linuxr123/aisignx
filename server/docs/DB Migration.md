# AISignX Database Migration

## Running Migrations

Run the following command in your activated virtual environment in the `AISignX/AISignX` folder where `app.py` is located:

```bash
python migration.py
```

This applies any pending schema changes to the database. It is safe to run on an already up-to-date database — if no changes are needed, it will report nothing to migrate.

---

## When to Run Migrations

Run `migration.py` every time you upgrade the server code. See [UPGRADE.md](UPGRADE.md) for the full upgrade procedure.

---

## Troubleshooting

- **"table already exists" or "column already exists"** — The migration has already been applied. This is safe to ignore.
- **"OperationalError: no such table"** — Run `python migration.py` to create missing tables.
- **Migration fails unexpectedly** — Restore your database backup before retrying. See the [Rolling Back](UPGRADE.md#rolling-back) section in the Upgrade Guide.

