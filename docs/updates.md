# Updates

There are two different things here: updating the **server** and updating the **apps on the screens**.

## Server: what exists now

- **Update check** (Admin > Updates): once a day the server asks the public GitHub releases page for the latest
  release of `linuxr123/aisignx` (override with `AISIGNX_UPDATE_REPO=owner/name`). If a newer version exists,
  admins see the version, the release notes and a link to the release on that page. **Check now** asks straight
  away. It is one anonymous request; nothing about your installation is sent. Turn it off on the Updates page
  (**Turn the check off**) or with `AISIGNX_UPDATE_CHECK=0`; the variable wins over the page. Only the version,
  the notes and links that point at that repository's releases are kept. The check needs the background jobs
  (`AISIGNX_JOBS` not `0`) for the daily run.
- **How to upgrade today:** get the new version and run the server installer again
  ([installing-the-server.md](installing-the-server.md)), or for Docker `git pull` and
  `docker compose up -d --build` ([docker.md](docker.md)). Both back up the database and keep your data.

## Server: not built yet, one-click install

The **signed one-click server update is not built.** The Updates page has no install button. The planned
button downloads the release, verifies the signature, backs up the database and upgrades. It needs a small
privileged helper (the service account intentionally cannot write to the program folder), so it is being
designed separately and will only work for servers set up with the installer scripts. The program has the code
that verifies a release signature, but nothing calls it to install anything, and no signing key has been
generated yet (`update_key.pub` does not exist), so the server cannot install anything by itself, by design.

## Screens: pushing an app update (built)

Updating the Windows or Android **app** on a screen from the admin is built:

1. Upload the new installer on **Downloads** ([downloads.md](downloads.md)): the APK for Android or the Windows
   installer. The page reads the version from the file (exactly for an APK, from the file name for the
   Windows installer); type one if it could not.
2. The **Displays** list shows a note when screens run an app older than the newest installer of their kind,
   and a screen's page shows **Update available** with the version it runs. An Android app that reported its
   signing key is compared with the newest installer signed with that key, so a debug and a release build can
   both be listed (see [downloads.md](downloads.md)).
3. An admin opens the screen and presses **Push the update**. This needs the **Update** switch on the screen's
   edit page (Edit > Device agent) and an app that reports it can update itself. On Windows only the
   **privileged agent** ([windows-agent.md](windows-agent.md)) can update the player; the player alone reports that
   it cannot, and the button stays off. On Android the app reports it (see [android-kiosk.md](android-kiosk.md)).
4. The server sends a signed command that names the version, size and SHA-256 of the installer
   ([signed-commands.md](signed-commands.md)). The screen downloads that file from the server only while the
   command is open (15 minutes), checks size and SHA-256, installs it and restarts. On Windows the agent installs
   silently and also requires a valid signature from the pinned code-signing certificate; Android installs silently
   on a Device Owner device and otherwise asks the person at the screen.

Limits:

- Android only accepts an update signed with the same key as the installed app, so keep to one build type per
  device.
- A screen that is switched off or offline picks the command up when it checks in, but the command expires
  after 15 minutes.
- The server upgrade does not change the apps; they are updated only by this push (or by hand).
- This path has been tried only on the owner's own test devices (a Galaxy Tab and a Shield). It has not been
  run on a real Windows screen or on a fleet. Push to one screen first.

## Signed releases (for the publisher)

A server never trusts a file just because it came from GitHub. Release files are meant to be signed with an
Ed25519 key that only the publisher holds; the matching public key is built into the server
(`server/aisignx/updates/update_key.pub`). This is preparation for the one-click update above.

1. Once: `python tools/release-sign.py keygen`. Keep `release-key.private` offline and backed up; commit the
   public key file.
2. For each release: build the zip, then `python tools/release-sign.py sign aisignx-server-<version>.zip`.
   Upload the zip and the `.sig` to the GitHub release.
3. Anyone can check: `python tools/release-sign.py verify aisignx-server-<version>.zip`.

If the private key is lost or leaked, publish a new server version with a new public key by hand (servers
cannot trust an automatic key change), and delete the old key.
