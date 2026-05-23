# AISignX display clients

Licensed under [AGPL-3.0-or-later](../../LICENSE). Native wrappers around the server’s web player (`/display/<token>`).

| Folder | Platform |
|--------|----------|
| [electron-client/](electron-client/) | Windows and Linux kiosk (Electron) |
| [android-client/](android-client/) | Android (WebView) |

## Build and publish

Run the build scripts from the **repository root** (not from this folder):

- Windows: `build_clients_windows.ps1` (`-Help`, `-Electron`, `-Android`, `-NoBump`, `-BumpOnly`)
- Linux: `build_clients_linux.sh` (`--help`, `--electron`, `--android`, `--no-bump`, `--bump-only`)

Default: bump versions, build both, copy to `server/static/clients/`.

See [../server/docs/CLIENTS.md](../server/docs/CLIENTS.md) for setup, signing, and release steps.
