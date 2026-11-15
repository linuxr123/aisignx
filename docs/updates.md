# Updates

## What exists now

- **Update check** (Admin > Updates): once a day the server asks the public GitHub releases page for the latest
  release of `linuxr123/aisignx` (override with `AISIGNX_UPDATE_REPO=owner/name`). If a newer version exists,
  admins see a banner with the release notes. It is one anonymous request; nothing about your installation is
  sent. Turn it off on the Updates page or with `AISIGNX_UPDATE_CHECK=0`. Only the version, the notes and links
  that point at that repository's releases are kept.
- **How to upgrade today:** get the new version and run the server installer again
  ([installing-the-server.md](installing-the-server.md)); it backs up the database and keeps your data.

## Signed releases (for the publisher)

A server never trusts a file just because it came from GitHub. Release files are signed with an Ed25519 key that
only the publisher holds; the matching public key is built into the server
(`server/aisignx/updates/update_key.pub`).

1. Once: `python tools/release-sign.py keygen`. Keep `release-key.private` offline and backed up; commit the
   public key file.
2. For each release: build the zip, then `python tools/release-sign.py sign aisignx-server-<version>.zip`.
   Upload the zip and the `.sig` to the GitHub release.
3. Anyone can check: `python tools/release-sign.py verify aisignx-server-<version>.zip`.

If the private key is lost or leaked, publish a new server version with a new public key by hand (servers
cannot trust an automatic key change), and delete the old key.

## Not built yet: one-click install

The planned button downloads the release, verifies the signature, backs up the database and upgrades. It needs a
small privileged helper (the service account intentionally cannot write to the program folder), so it is
being designed separately and will only work for servers set up with the installer scripts. Until a signing key
exists and a signed release is published, the server cannot install anything by itself, by design.
