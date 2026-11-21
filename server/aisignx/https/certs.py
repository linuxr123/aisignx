"""TLS certificates for the web page: make a self-signed one, or check and keep one you bought.

Pure functions plus a small folder of files (`server.crt`: the certificate and its chain, `server.key`: the private
key). nginx reads those two files. Nothing here talks to a network or runs a program.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import re
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERT_FILE = "server.crt"
KEY_FILE = "server.key"
SELF_SIGNED_DAYS = 1095          # 3 years: a screen that pinned the certificate should not break every year
WARN_DAYS = 30
MAX_PEM_BYTES = 64 * 1024
_HOST_RE = re.compile(r"^(\*\.)?(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


class CertError(ValueError):
    """A certificate or key that cannot be used; the message is meant for the admin."""


def clean_names(raw) -> list[str]:
    """Host names and IP addresses from a text (commas, spaces or lines) or a list. Raises CertError."""
    items = re.split(r"[,\s;]+", raw) if isinstance(raw, str) else list(raw or [])
    out: list[str] = []
    for item in (str(i).strip() for i in items):
        if not item:
            continue
        try:
            item = str(ipaddress.ip_address(item.strip("[]")))
        except ValueError:
            if not _HOST_RE.match(item):
                raise CertError(f"'{item[:60]}' is not a host name or an IP address.")
            item = item.lower()
        if item not in out:
            out.append(item)
    if not out:
        raise CertError("Enter at least one host name or IP address.")
    if len(out) > 20:
        raise CertError("At most 20 names.")
    return out


def _san(names: list[str]) -> x509.SubjectAlternativeName:
    entries: list[x509.GeneralName] = []
    for n in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            entries.append(x509.DNSName(n))
    return x509.SubjectAlternativeName(entries)


def make_self_signed(names: list[str], days: int = SELF_SIGNED_DAYS, now: dt.datetime | None = None) -> tuple[bytes, bytes]:
    """(certificate PEM, private key PEM): ECDSA P-256, valid for `days`, for the given names and addresses."""
    names = clean_names(names)
    now = now or dt.datetime.now(dt.timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    cn = names[0][:64]
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AiSignX")])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=days))
            .add_extension(_san(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def load_chain(pem: bytes | str) -> list[x509.Certificate]:
    data = pem.encode() if isinstance(pem, str) else pem
    if len(data) > MAX_PEM_BYTES:
        raise CertError("That certificate file is too large.")
    if b"PRIVATE KEY" in data:
        raise CertError("That looks like a private key, not a certificate. Put the certificate in this box and the key in the other.")
    try:
        chain = x509.load_pem_x509_certificates(data)
    except Exception:
        chain = []
    if not chain:
        raise CertError("No certificate found. It must be PEM text starting with -----BEGIN CERTIFICATE-----. A .pfx or .p12 file must be converted first (see docs/https.md).")
    return chain


def load_key(pem: bytes | str, password: str | None = None):
    data = pem.encode() if isinstance(pem, str) else pem
    if len(data) > MAX_PEM_BYTES:
        raise CertError("That key file is too large.")
    try:
        return serialization.load_pem_private_key(data, password=password.encode() if password else None)
    except TypeError:
        raise CertError("The private key is protected by a passphrase. Enter it, or remove it first.")
    except Exception:
        raise CertError("The private key could not be read (it must be PEM text starting with -----BEGIN ... PRIVATE KEY-----, and the passphrase must be right).")


def _names(cert: x509.Certificate) -> list[str]:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        out = [str(i) for i in san.get_values_for_type(x509.IPAddress)] + san.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        out = []
    if not out:
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        out = [cn[0].value] if cn else []
    return out


def describe(cert: x509.Certificate, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    end = cert.not_valid_after_utc
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    issuer = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    return {"subject": cn[0].value if cn else "", "issuer": issuer[0].value if issuer else "", "names": _names(cert),
            "not_before": cert.not_valid_before_utc, "not_after": end, "days_left": (end - now).days,
            "self_signed": cert.issuer == cert.subject,
            "fingerprint": cert.fingerprint(hashes.SHA256()).hex().upper()}


def fingerprint_text(hex_fp: str) -> str:
    return ":".join(hex_fp[i:i + 2] for i in range(0, len(hex_fp), 2))


def covers(names: list[str], host: str) -> bool:
    """Does one of the certificate's names (with *. wildcards) match `host`?"""
    host = host.lower()
    for n in names:
        n = n.lower()
        if n == host or (n.startswith("*.") and host.count(".") >= 1 and host.split(".", 1)[1] == n[2:]):
            return True
    return False


def check_pair(cert_pem, key_pem, password: str | None = None, now: dt.datetime | None = None):
    """Validates an imported certificate and key. Returns (chain, key, problems) where problems are warnings.
    Raises CertError when the pair cannot be used."""
    now = now or dt.datetime.now(dt.timezone.utc)
    chain = load_chain(cert_pem)
    key = load_key(key_pem, password)
    leaf = chain[0]
    pub = lambda k: k.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)  # noqa: E731
    if pub(leaf.public_key()) != pub(key.public_key()):
        hint = " The first certificate in the file must be yours (the one for your host name), then the intermediate ones." if len(chain) > 1 else ""
        raise CertError("The private key does not belong to this certificate." + hint)
    if isinstance(key, rsa.RSAPrivateKey) and key.key_size < 2048:
        raise CertError("The RSA key is shorter than 2048 bits.")
    if leaf.not_valid_after_utc <= now:
        raise CertError(f"This certificate expired on {leaf.not_valid_after_utc:%Y-%m-%d}.")
    problems: list[str] = []
    if leaf.not_valid_before_utc > now + dt.timedelta(minutes=10):
        problems.append(f"It is not valid before {leaf.not_valid_before_utc:%Y-%m-%d}: check this computer's clock.")
    if len(chain) == 1 and leaf.issuer != leaf.subject:
        problems.append("Only one certificate was given. Certificates from a certificate authority normally need the intermediate certificate(s) after it in the same file; without them some devices will not trust the site.")
    for c in chain[1:]:
        try:
            c.extensions.get_extension_for_class(x509.BasicConstraints)
        except x509.ExtensionNotFound:
            problems.append("A certificate after the first one is not a certificate authority certificate: check the order in the file.")
            break
    return chain, key, problems


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def store(folder: Path, chain: list[x509.Certificate], key) -> None:
    """Writes the certificate chain and the (unencrypted, PKCS#8) private key for nginx. The key is readable by the
    owner only where the system allows it (Linux); on Windows the folder's permissions decide (setup-nginx.ps1 sets them)."""
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass
    pem = b"".join(c.public_bytes(serialization.Encoding.PEM) for c in chain)
    keypem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    _write_atomic(folder / KEY_FILE, keypem, 0o600)     # the key first: a crash must not leave a new cert with an old key
    _write_atomic(folder / CERT_FILE, pem, 0o644)


def current(folder: Path, now: dt.datetime | None = None) -> dict | None:
    """The stored certificate described, or None when there is none (or it cannot be read)."""
    try:
        chain = load_chain((folder / CERT_FILE).read_bytes())
    except (OSError, CertError):
        return None
    info = describe(chain[0], now)
    info["chain_length"] = len(chain)
    info["has_key"] = (folder / KEY_FILE).is_file()
    return info
