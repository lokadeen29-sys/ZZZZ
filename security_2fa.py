"""
V51 TASK B — TOTP 2FA for admin accounts.

This module is the SINGLE source of truth for:
  - generating TOTP secrets / provisioning URIs / QR codes
  - verifying TOTP codes (with a small clock-skew window)
  - generating and verifying one-time backup codes
  - persistence helpers that delegate to `database.py`

Design notes
------------
- TOTP secrets are stored in plaintext in the DB (standard practice —
  they must be recoverable to compute HMAC). File permissions on the
  SQLite file protect them; attackers with DB read already own the app.
- Backup codes are stored as PBKDF2 hashes (via werkzeug) so a DB leak
  does NOT reveal unused backup codes. Each code is usable exactly once.
- We use SHA1 + 6 digits + 30s period (the RFC 6238 defaults) so the
  secret works with every mainstream authenticator (Google, Authy,
  1Password, Aegis, Microsoft Authenticator, …).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import secrets as _pysecrets
from typing import List, Optional, Tuple

import pyotp
import qrcode
import qrcode.image.svg
from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger("tecnogems.2fa")

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
TOTP_ISSUER = "TecnoGems Admin"
BACKUP_CODE_COUNT = 10
BACKUP_CODE_LEN = 10  # hex chars, grouped 5-5 for readability
# pyotp default window is 1 (accepts previous + next 30s slot). We keep that
# to tolerate mild clock drift without being too permissive.
TOTP_VALID_WINDOW = 1


# -----------------------------------------------------------------------------
# Secret / provisioning URI
# -----------------------------------------------------------------------------
def generate_totp_secret() -> str:
    """Return a fresh base32 TOTP secret (160 bits)."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_label: str) -> str:
    """Return the otpauth:// URI that authenticator apps scan.

    `account_label` is typically the admin's email. It is shown inside the
    authenticator app next to the issuer — keep it readable.
    """
    return pyotp.TOTP(secret).provisioning_uri(
        name=account_label, issuer_name=TOTP_ISSUER
    )


def qr_svg(uri: str) -> str:
    """Render the provisioning URI as an inline SVG string.

    SVG is used instead of PNG so we avoid a Pillow runtime call on every
    setup page load and can embed the QR directly in the HTML without a
    data: URL (CSP friendly).
    """
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(uri, image_factory=factory, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


# -----------------------------------------------------------------------------
# TOTP verification
# -----------------------------------------------------------------------------
def verify_totp(secret: str, code: str) -> bool:
    """Return True if `code` is a currently-valid TOTP for `secret`."""
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "").replace("-", "")
    if not code.isdigit() or len(code) != 6:
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=TOTP_VALID_WINDOW)
    except Exception:  # pragma: no cover — defensive
        log.exception("TOTP verify raised unexpectedly")
        return False


# -----------------------------------------------------------------------------
# Backup codes
# -----------------------------------------------------------------------------
def _format_backup_code(raw: str) -> str:
    """Display form: 'abcde-fghij' (lowercase, dash-separated)."""
    raw = raw.lower()
    return f"{raw[:5]}-{raw[5:]}"


def _normalize_backup_code(code: str) -> str:
    """Strip whitespace/dashes so '  ABCDE-FGHIJ ' compares equal to its raw."""
    return "".join(ch for ch in (code or "").lower() if ch.isalnum())


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> Tuple[List[str], List[str]]:
    """Return (plain_codes, hashed_codes).

    `plain_codes` is shown to the user EXACTLY ONCE (after setup or
    regeneration). `hashed_codes` is what we persist.
    """
    plain: List[str] = []
    hashed: List[str] = []
    for _ in range(count):
        raw = _pysecrets.token_hex(BACKUP_CODE_LEN // 2)  # 5 bytes -> 10 hex
        plain.append(_format_backup_code(raw))
        hashed.append(generate_password_hash(raw, method="pbkdf2:sha256"))
    return plain, hashed


def serialize_backup_codes(hashed_codes: List[str]) -> str:
    """JSON-serialise the list of hashes for DB storage."""
    return json.dumps(hashed_codes, separators=(",", ":"))


def deserialize_backup_codes(blob: Optional[str]) -> List[str]:
    if not blob:
        return []
    try:
        data = json.loads(blob)
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:
        log.warning("Malformed backup-codes blob in DB; treating as empty.")
    return []


def consume_backup_code(hashed_codes: List[str], submitted: str) -> Optional[List[str]]:
    """If `submitted` matches one of `hashed_codes`, return the list WITHOUT
    that code (so the caller can persist it). Otherwise return None.

    NOTE: we iterate the full list even after a match to avoid a timing
    oracle that leaks which slot matched. This is cheap (10 hashes).
    """
    if not submitted or not hashed_codes:
        return None
    candidate = _normalize_backup_code(submitted)
    if len(candidate) != BACKUP_CODE_LEN:
        return None
    matched_index = -1
    for idx, h in enumerate(hashed_codes):
        try:
            if check_password_hash(h, candidate) and matched_index == -1:
                matched_index = idx
        except Exception:
            # defensive: skip malformed hashes
            continue
    if matched_index == -1:
        return None
    return [h for i, h in enumerate(hashed_codes) if i != matched_index]
