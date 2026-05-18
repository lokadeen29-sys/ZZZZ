"""V53 REFACTOR (phase 1): image upload + sanitisation helpers.

Extracted verbatim from app.py:271-441. The module exposes:

- ``ALLOWED_UPLOAD_EXTS``  — whitelist of accepted upload extensions.
- ``_IMG_MAGIC``           — magic-byte signatures for image type detection.
- ``_PROOF_MAGIC``         — magic-byte signatures for deposit-proof uploads.
- ``_detect_image_kind(head_bytes)``
- ``_ext_ok(filename)``
- ``_proof_magic_ok(file_stream)``
- ``process_upload_to_webp(file_storage, dest_dir, base_name, max_w, quality)``
- ``_sanitise_svg(svg_text)``

All Pillow imports are guarded so the helpers degrade gracefully when
Pillow is missing (the same fallback behaviour app.py had).

History:
- PATCH-H1: SVG XSS sanitiser (strips <script>, on* handlers, data:/javascript:).
- PATCH-H3: Image.verify() before Image.open() to reject malformed images.
- PATCH-H4: magic-byte verification for deposit proofs.
- PATCH-L5: Pillow >= 10 Resampling enum compat.
- PATCH-M3: 25 MP cap to prevent decompression bomb DoS.
- V43: WebP auto-conversion on upload.
- V50.2 LOW: PDF removed from allowed-uploads (XSS/JS payload risk).
"""
from __future__ import annotations

import logging
import os
import re as _re_svg

log = logging.getLogger("tecnogems.images")

# ---------------------------------------------------------------------------
# Pillow (optional)
# ---------------------------------------------------------------------------
try:
    from PIL import Image, ImageOps  # type: ignore[import-not-found]
    # PATCH-M3: cap decompression to prevent "image bomb" DoS
    Image.MAX_IMAGE_PIXELS = 25_000_000  # ~25 MP, plenty for any UI image
    _PIL_OK = True
except Exception:  # pragma: no cover - exercised only when Pillow is missing
    _PIL_OK = False
    log.warning("Pillow not installed. Image auto-conversion disabled. Run: pip install Pillow")

# ---------------------------------------------------------------------------
# Magic-byte signatures (real type check, not just file extension)
# ---------------------------------------------------------------------------
_IMG_MAGIC = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",  # WEBP starts with RIFF....WEBP
}


def _detect_image_kind(head_bytes):
    if not head_bytes:
        return None
    for sig, kind in _IMG_MAGIC.items():
        if head_bytes.startswith(sig):
            if kind == "webp" and b"WEBP" not in head_bytes[:16]:
                continue
            return kind
    return None


def process_upload_to_webp(file_storage, dest_dir, base_name, max_w=1200, quality=82):
    """Read uploaded image, verify magic bytes, strip EXIF, downscale to
    ``max_w``, and save as WebP. Returns saved filename (e.g. ``"name.webp"``)
    or ``None`` on failure.

    Falls back to plain save (preserving original extension) if Pillow is
    unavailable.
    """
    try:
        head = file_storage.stream.read(32)
        file_storage.stream.seek(0)
        kind = _detect_image_kind(head)
        if not kind:
            return None
        os.makedirs(dest_dir, exist_ok=True)
        out_name = f"{base_name}.webp"
        out_path = os.path.join(dest_dir, out_name)
        if not _PIL_OK:
            # Fallback: just save original under original extension.
            ext = "webp" if kind == "webp" else kind
            out_name = f"{base_name}.{ext}"
            out_path = os.path.join(dest_dir, out_name)
            file_storage.save(out_path)
            return out_name
        # PATCH-H3: verify the file is a valid, non-malicious image BEFORE
        # decoding the full payload. Image.verify() consumes the file so we
        # must reopen for actual processing.
        try:
            _verify_img = Image.open(file_storage.stream)
            _verify_img.verify()
        except Exception as exc:
            log.warning("process_upload_to_webp verify failed: %s", exc)
            return None
        try:
            file_storage.stream.seek(0)
        except Exception:
            return None
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        if img.width > max_w:
            ratio = max_w / float(img.width)
            new_h = max(1, int(img.height * ratio))
            # PATCH-L5: use new Resampling enum (Pillow >= 10) with fallback.
            _resample = getattr(Image, "Resampling", Image).LANCZOS
            img = img.resize((max_w, new_h), _resample)
        save_kwargs = {"quality": quality, "method": 6}
        if img.mode == "RGBA":
            save_kwargs["lossless"] = False
        img.save(out_path, "WEBP", **save_kwargs)
        return out_name
    except Exception as exc:
        log.warning("process_upload_to_webp failed: %s", exc)
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Allowed extensions and deposit-proof magic-byte checks
# ---------------------------------------------------------------------------
# V50.2 LOW: removed "pdf" from deposit-proof allowed extensions. PDFs
# can embed JavaScript and are a common malware vector; images are
# sufficient for a payment-proof screenshot and much safer to serve back.
ALLOWED_UPLOAD_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}


def _ext_ok(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_UPLOAD_EXTS


# PATCH-H4: magic-byte verification for deposit proofs (prevents file-type
# spoofing such as evil.php renamed to evil.png).
# V50.2 LOW: PDF removed - images only.
_PROOF_MAGIC = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}


def _proof_magic_ok(file_stream):
    """Verify uploaded file's first bytes match an accepted media type.
    Resets stream position to 0 before returning so the caller can save it."""
    try:
        head = file_stream.read(16)
        file_stream.seek(0)
    except Exception:
        return False
    if not head:
        return False
    for sig, _kind in _PROOF_MAGIC.items():
        if head.startswith(sig):
            return True
    # WebP starts with RIFF....WEBP
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return True
    return False


# ---------------------------------------------------------------------------
# SVG sanitiser (PATCH-H1)
# ---------------------------------------------------------------------------
# Strips <script>, on* event handlers, and javascript:/data: URIs from
# admin-uploaded SVGs to prevent stored XSS.
_SVG_SCRIPT_RE = _re_svg.compile(
    r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>",
    _re_svg.IGNORECASE | _re_svg.DOTALL,
)
_SVG_EVENT_RE = _re_svg.compile(
    r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
    _re_svg.IGNORECASE,
)
_SVG_JS_URI_RE = _re_svg.compile(
    r"(href|xlink:href|src)\s*=\s*(\"|')\s*(javascript|data):[^\"']*(\"|')",
    _re_svg.IGNORECASE,
)
_SVG_FOREIGN_RE = _re_svg.compile(
    r"<\s*(foreignObject|iframe|object|embed)\b[^>]*>.*?<\s*/\s*\1\s*>",
    _re_svg.IGNORECASE | _re_svg.DOTALL,
)


def _sanitise_svg(svg_text):
    """Best-effort SVG XSS sanitiser. Removes scripts, event handlers,
    foreignObject / iframe nodes, and javascript:/data: URLs."""
    if not svg_text:
        return ""
    s = _SVG_SCRIPT_RE.sub("", svg_text)
    s = _SVG_FOREIGN_RE.sub("", s)
    s = _SVG_EVENT_RE.sub("", s)
    s = _SVG_JS_URI_RE.sub(r"\1=\2#\4", s)
    return s


__all__ = [
    "ALLOWED_UPLOAD_EXTS",
    "_IMG_MAGIC",
    "_PROOF_MAGIC",
    "_PIL_OK",
    "_detect_image_kind",
    "_ext_ok",
    "_proof_magic_ok",
    "process_upload_to_webp",
    "_sanitise_svg",
]
