"""Agency logos for the email template.

The bytes live outside the agent record (DynamoDB items cap at 400 KB and the
record is read on every tool call): in a private S3 bucket when
HOMEFRONT_LOGO_BUCKET is set, else in process memory (lost on restart, like
AGENT_STORE=memory). The agent record keeps only the metadata from
`validate_logo` plus the object key.

Emails embed the logo as an inline (cid:) attachment, so the bucket never needs
public access or presigned links, and mail clients show it without the
"download images" prompt.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import struct
import time
from threading import Lock
from typing import Any, Protocol

MAX_LOGO_BYTES = 512 * 1024
MAX_DIMENSION = 4000

# SVG is refused: Gmail and Outlook don't render it, and it can carry script.
_FORMATS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}


class InvalidLogoError(ValueError):
    pass


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 24 and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    return None


def _gif_size(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length field
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        # SOF0..SOF15 except DHT (C4), JPG (C8) and DAC (CC) carry the frame size.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    return None


def sniff(data: bytes) -> tuple[str, tuple[int, int] | None] | None:
    """(content type, (width, height)) from the file's own bytes, never its name."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", _png_size(data)
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", _jpeg_size(data)
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", _gif_size(data)
    return None


def decode_upload(data_base64: str) -> bytes:
    raw = (data_base64 or "").strip()
    if raw.startswith("data:"):  # tolerate a data URL from FileReader
        raw = raw.partition(",")[2]
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidLogoError("The upload isn't valid base64.")


def validate_logo(data: bytes) -> dict[str, Any]:
    """Metadata for a logo, or InvalidLogoError saying what's wrong with it."""
    if not data:
        raise InvalidLogoError("The file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        raise InvalidLogoError(f"Logos are limited to {MAX_LOGO_BYTES // 1024} KB; this one is {len(data) // 1024} KB.")
    found = sniff(data)
    if not found:
        raise InvalidLogoError("Upload a PNG, JPEG or GIF image (SVG doesn't display in most email clients).")
    content_type, size = found
    if not size or not all(size):
        raise InvalidLogoError("Couldn't read the image's dimensions; the file may be damaged.")
    width, height = size
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise InvalidLogoError(f"Images are limited to {MAX_DIMENSION}×{MAX_DIMENSION} pixels.")
    return {
        "content_type": content_type,
        "size": len(data),
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(data).hexdigest(),
        "uploaded_at": time.time(),
    }


def object_key(agent_id: str, meta: dict[str, Any]) -> str:
    return f"logos/{agent_id}/{meta['sha256'][:16]}.{_FORMATS[meta['content_type']]}"


class LogoRepository(Protocol):
    backend: str

    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes | None: ...

    def delete(self, key: str) -> None: ...


class MemoryLogoRepository:
    backend = "memory"

    def __init__(self) -> None:
        self._items: dict[str, bytes] = {}
        self._lock = Lock()

    def put(self, key: str, data: bytes, content_type: str) -> None:
        with self._lock:
            self._items[key] = bytes(data)

    def get(self, key: str) -> bytes | None:
        with self._lock:
            return self._items.get(key)

    def delete(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)


class S3LogoRepository:
    """Private bucket; the backend's IAM role needs s3:GetObject/PutObject/DeleteObject."""

    backend = "s3"

    def __init__(self, bucket: str, client: Any = None, region: str | None = None) -> None:
        self.bucket = bucket
        if client is None:
            import boto3
            from botocore.config import Config

            config = Config(retries={"max_attempts": 2}, connect_timeout=3, read_timeout=10)
            client = boto3.client("s3", region_name=region, config=config)
        self._s3 = client

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )

    def get(self, key: str) -> bytes | None:
        try:
            return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:
            code = ((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code")
            if code in ("NoSuchKey", "404"):
                return None
            raise

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=key)
