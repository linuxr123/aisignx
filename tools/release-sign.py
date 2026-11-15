#!/usr/bin/env python3
"""Sign release files so servers can verify them. Run this on YOUR machine only.

  python tools/release-sign.py keygen  [--out release-key.private]
      Make a key pair. Keep the private file offline and backed up; never commit it.
      The public key is printed and written to server/aisignx/updates/update_key.pub
      (commit that file: it is built into every server and is how they trust releases).

  python tools/release-sign.py sign FILE [--key release-key.private]
      Writes FILE.sig (base64). Upload FILE and FILE.sig to the GitHub release.

  python tools/release-sign.py verify FILE
      Checks FILE against FILE.sig and the built-in public key.
"""
import argparse
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PUB = Path(__file__).resolve().parents[1] / "server" / "aisignx" / "updates" / "update_key.pub"


def load_private(path: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(Path(path).read_text().strip()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen"); k.add_argument("--out", default="release-key.private")
    s = sub.add_parser("sign"); s.add_argument("file"); s.add_argument("--key", default="release-key.private")
    v = sub.add_parser("verify"); v.add_argument("file")
    a = ap.parse_args()
    if a.cmd == "keygen":
        if Path(a.out).exists() or PUB.exists():
            print("A key already exists; refusing to overwrite it (delete it first if you mean to rotate).", file=sys.stderr)
            return 1
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(base64.b64encode(raw).decode() + "\n")
        PUB.write_text(base64.b64encode(pub).decode() + "\n", encoding="ascii")
        print(f"Private key: {a.out}  (keep it offline, back it up, never commit it)")
        print(f"Public key:  {PUB}  (commit this)")
        return 0
    data = Path(a.file).read_bytes()
    if a.cmd == "sign":
        sig = base64.b64encode(load_private(a.key).sign(data)).decode()
        Path(a.file + ".sig").write_text(sig + "\n", encoding="ascii")
        print(f"Wrote {a.file}.sig")
        return 0
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUB.read_text().strip()))
    try:
        pub.verify(base64.b64decode(Path(a.file + ".sig").read_text().strip()), data)
    except Exception:
        print("BAD signature", file=sys.stderr)
        return 1
    print("Signature OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
