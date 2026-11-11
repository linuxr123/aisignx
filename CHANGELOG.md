# Changelog

## Unreleased (v3)

v3 is a clean rewrite of AiSignX as a single-organization app.

### Added
- Phase 1 (Core): Flask app factory, environment-based config, Alembic migrations.
- First-run setup wizard that creates the admin account, organization name and time zone (no default passwords).
- Login and logout (POST with CSRF), "keep me signed in", safe `next` redirects, and a profile page for changing your password.
- User management for admins: create, edit, disable and delete users with the roles `admin`, `editor` and `viewer`. Admins cannot demote, disable or delete themselves.
- Settings page (organization name, time zone).
- Bootstrap 5 base layout served locally (works offline) and a `/healthz` endpoint.
- pytest suite that builds the schema through Alembic migrations.
- Phase 2 (Content): media library with multi-file image and video upload, web pages by URL, file validation (Pillow / ffprobe), thumbnails, detected video length and resolution, search and type filter.
- Playlists with drag-and-drop ordering (plus up/down buttons), per-item duration, transition, fit mode and video mute, a playlist default transition (including random), duplicate, and total runtime.
- Setting for the default duration of new images and web pages.
- Web page previews: screenshots taken in the background with the installed Edge, Chrome or Chromium (headless, no extra Python packages), a title card when a page can't be captured, a "Refresh preview" button, and live refresh in the media list.
- Install scripts `scripts/install.ps1` (Windows, winget) and `scripts/install.sh` (Linux/macOS) that install Python packages, ffmpeg and a browser if missing, then set up the database.
- `flask --app wsgi check` command and an admin dashboard warning for missing programs.
- Uploads are stored in `server/uploads` (`AISIGNX_UPLOAD_DIR`), with a size limit from `AISIGNX_MAX_UPLOAD_MB` (default 2048).
