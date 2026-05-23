# Security Policy

## Supported versions

Security fixes are applied to the latest release on the default branch. Upgrade to the current version when possible.

## Reporting a vulnerability

**Do not** open a public GitHub issue for security vulnerabilities.

Instead, contact the maintainers privately with:

- Description of the issue and impact
- Steps to reproduce
- Affected version or commit
- Suggested fix (if any)

We will acknowledge receipt and work on a fix before public disclosure when appropriate.

## Secrets and credentials

Never commit:

- `server/config.py` with production secrets
- `.env` files with real values
- Android signing keystores or `keystore.properties`
- Display registration tokens, API keys, or passwords in docs or code

Use `.env.example`, `server/config.example.py`, and `clients/android-client/keystore.properties.example` as templates only.

## Default credentials

The bootstrap admin account (`admin` / `Admin123!`) is for **first login only**. Change the password immediately after installation.
