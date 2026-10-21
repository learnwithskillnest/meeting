"""Private media storage with signed, expiring URLs.

Files are stored outside the public static directory. The only way to read a
file is through /media/<key>?e=<expiry>&s=<signature>, and signatures are only
issued to viewers who passed an authorization check.

Images are decoded and re-encoded with Pillow: this rejects non-images and
polyglot files, and strips EXIF metadata (including GPS location).
"""
from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .. import db as dbm
from ..config import get_settings
from ..security import sign, verify_signature

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MAX_DIMENSION = 2048


class UploadRejected(Exception):
    pass


@dataclass
class StoredImage:
    key: str
    mime: str
    size: int
    width: int
    height: int


def _root() -> Path:
    root = Path(get_settings().media_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path_for(key: str) -> Path:
    if not key or ".." in key or key.startswith("/") or "\\" in key:
        raise ValueError("invalid storage key")
    path = (_root() / key).resolve()
    if not str(path).startswith(str(_root().resolve())):
        raise ValueError("invalid storage key")
    return path


MAX_PIXELS = Image.MAX_IMAGE_PIXELS  # Pillow only *errors* above 2x this; we refuse at 1x


def process_image(data: bytes, max_bytes: int | None = None) -> tuple[bytes, int, int]:
    max_bytes = max_bytes or get_settings().max_upload_bytes
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > max_bytes:
        raise UploadRejected(f"Images must be smaller than {max_bytes // (1024 * 1024)} MB.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            w, h = probe.size
            # Small files can still decode to huge bitmaps ("decompression bombs"):
            # refuse from the header, before any pixel data is processed.
            if w * h > MAX_PIXELS:
                raise UploadRejected("That image's dimensions are too large. Please use a photo under 40 megapixels.")
            probe.verify()
    except Image.DecompressionBombError:
        raise UploadRejected("That image's dimensions are too large. Please use a photo under 40 megapixels.")
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise UploadRejected("That file isn't a supported image (JPEG, PNG or WebP).")
    if fmt not in ALLOWED_FORMATS:
        raise UploadRejected("Only JPEG, PNG and WebP images are allowed.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
            out = io.BytesIO()
            # Re-encode as JPEG without metadata.
            img.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue(), img.width, img.height
    except (OSError, Image.DecompressionBombError):
        raise UploadRejected("The image could not be processed.")


def save_image(prefix: str, data: bytes) -> StoredImage:
    clean, width, height = process_image(data)
    key = f"{prefix}/{dbm.new_id()}.jpg"
    path = _path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(clean)
    return StoredImage(key=key, mime="image/jpeg", size=len(clean), width=width, height=height)


def delete(key: str) -> None:
    try:
        os.remove(_path_for(key))
    except (FileNotFoundError, ValueError):
        pass


def read(key: str) -> bytes | None:
    try:
        return _path_for(key).read_bytes()
    except (FileNotFoundError, ValueError):
        return None


def signed_url(key: str, ttl: int | None = None) -> str:
    ttl = ttl or get_settings().signed_url_ttl
    # Round expiry to a window so URLs are cache-friendly within it.
    exp = (int(time.time()) // 60 + 1) * 60 + ttl
    return f"/media/{key}?e={exp}&s={sign(f'media:{key}:{exp}')}"


def check_signature(key: str, exp: str, sig: str) -> bool:
    try:
        if int(exp) < time.time():
            return False
    except (TypeError, ValueError):
        return False
    return verify_signature(f"media:{key}:{exp}", sig)
