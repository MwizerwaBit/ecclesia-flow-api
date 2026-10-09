"""Private file storage for documents that must never be publicly reachable.

Unlike `media_uploads/` (served statically for avatars and event covers),
files here are only ever returned by an endpoint that has checked the caller's
permission. Storage keys are random UUIDs — never the uploaded file name — and
the file type is decided by the bytes, not by what the client claims.
"""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import AppError

# Magic numbers of the formats accepted for official documents.
_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
]


class UnsupportedFileError(AppError):
    status_code = 415
    code = "unsupported_file"


class FileTooLargeError(AppError):
    status_code = 413
    code = "file_too_large"


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    mime_type: str
    size_bytes: int
    sha256: str


def _root() -> Path:
    return Path(get_settings().private_files_dir).resolve()


def sniff(data: bytes) -> tuple[str, str]:
    for signature, mime, ext in _SIGNATURES:
        if data.startswith(signature):
            return mime, ext
    raise UnsupportedFileError("Upload a PDF, PNG or JPEG file")


# Public media (avatars, logos, event covers, recordings). Served from the
# API's own origin, so anything a browser would execute — HTML, SVG, scripts —
# is refused, and the stored extension comes from these bytes, never from the
# uploaded file name (a "photo.html" must not be served as a web page).
_MEDIA_SIGNATURES: list[tuple[bytes, int, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", ".png"),
    (b"\xff\xd8\xff", 0, "image/jpeg", ".jpg"),
    (b"GIF87a", 0, "image/gif", ".gif"),
    (b"GIF89a", 0, "image/gif", ".gif"),
    (b"WEBP", 8, "image/webp", ".webp"),  # after "RIFF....": checked below
    (b"%PDF-", 0, "application/pdf", ".pdf"),
    (b"ftyp", 4, "video/mp4", ".mp4"),
    (b"\x1a\x45\xdf\xa3", 0, "video/webm", ".webm"),
    (b"ID3", 0, "audio/mpeg", ".mp3"),
]


def sniff_media(data: bytes) -> tuple[str, str]:
    for signature, offset, mime, ext in _MEDIA_SIGNATURES:
        if data[offset : offset + len(signature)] == signature:
            if mime == "image/webp" and not data.startswith(b"RIFF"):
                continue
            return mime, ext
    raise UnsupportedFileError("Upload an image (PNG, JPEG, GIF, WebP), a PDF, or MP4/WebM/MP3 media")


def save(tenant_id: str, data: bytes) -> StoredFile:
    settings = get_settings()
    if len(data) == 0:
        raise UnsupportedFileError("The file is empty")
    if len(data) > settings.max_document_bytes:
        raise FileTooLargeError(f"Files can be at most {settings.max_document_bytes // (1024 * 1024)} MB")
    mime, ext = sniff(data)
    key = f"{uuid.UUID(tenant_id)}/{uuid.uuid4()}{ext}"
    path = _root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return StoredFile(storage_key=key, mime_type=mime, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def read(storage_key: str, expected_sha256: str) -> bytes:
    """Reads a stored file, refusing anything outside the storage root and any
    file whose contents no longer match the fingerprint taken at upload."""
    root = _root()
    path = (root / storage_key).resolve()
    if root not in path.parents:
        raise UnsupportedFileError("Invalid storage key")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise AppError("The stored file failed its integrity check", code="integrity_error")
    return data
