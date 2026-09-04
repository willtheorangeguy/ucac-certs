"""Stored copies of certificates: the scans and photographs behind a roster row.

A scan proves a certification is current; the copy is what a manager hands to an
inspector who asks to see the card itself. Copies live on disk next to the
database rather than inside it, so one backup of the data directory catches both,
and a large PDF never has to be read through SQLite to be served.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAX_BYTES = 10 * 1024 * 1024
_CHUNK = 64 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class RejectedUpload(ValueError):
    """The upload is not something the application is willing to store."""


@dataclass(frozen=True)
class FileKind:
    content_type: str
    suffix: str
    label: str


PDF = FileKind("application/pdf", ".pdf", "PDF")
PNG = FileKind("image/png", ".png", "PNG")
JPEG = FileKind("image/jpeg", ".jpg", "JPEG")
GIF = FileKind("image/gif", ".gif", "GIF")
WEBP = FileKind("image/webp", ".webp", "WebP")
HEIC = FileKind("image/heic", ".heic", "HEIC")

ACCEPTED = "A copy must be a PDF or an image (PNG, JPEG, GIF, WebP or HEIC)."
TOO_LARGE = f"A copy must be {MAX_BYTES // (1024 * 1024)} MB or smaller."
# What the file picker offers. The check that matters is detect(); this only spares
# the manager from choosing a file that is going to be refused.
ACCEPT_ATTRIBUTE = "application/pdf,image/png,image/jpeg,image/gif,image/webp,image/heic"

_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


def detect(header: bytes) -> FileKind | None:
    """The kind of file these leading bytes belong to, or ``None`` for anything else.

    An upload is identified by what it contains, never by the name or content type
    the browser claims, so a script renamed to ``.pdf`` is refused rather than stored
    and later handed back to someone's browser.
    """
    if header.startswith(b"%PDF-"):
        return PDF
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if header.startswith(b"\xff\xd8\xff"):
        return JPEG
    if header.startswith((b"GIF87a", b"GIF89a")):
        return GIF
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return WEBP
    if header[4:8] == b"ftyp" and header[8:12] in _HEIC_BRANDS:
        return HEIC
    return None


def clean_filename(raw: str) -> str:
    """The upload's own name, reduced to something safe to store and to echo back.

    Only ever used for display and for the download's suggested name: the bytes are
    stored under a generated name, so a hostile filename has no path to act on.
    """
    name = _CONTROL.sub("", raw).replace("\\", "/").rsplit("/", 1)[-1]
    name = " ".join(name.split()).lstrip(".")
    return name[:120] or "certificate"


@dataclass(frozen=True)
class StoredFile:
    stored_name: str
    kind: FileKind
    size: int


class FileStore:
    """Uploaded copies on disk, one flat directory of generated names."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, stream: BinaryIO) -> StoredFile:
        """Write an upload to disk after checking what it is and how big it is.

        The size limit is enforced while streaming rather than from a declared
        Content-Length, which a caller controls and can understate.
        """
        first = stream.read(_CHUNK)
        kind = detect(first[:12])
        if kind is None:
            raise RejectedUpload(ACCEPTED)

        self.root.mkdir(parents=True, exist_ok=True)
        stored_name = f"{secrets.token_hex(16)}{kind.suffix}"
        path = self.root / stored_name
        size = 0
        try:
            with path.open("wb") as handle:
                chunk = first
                while chunk:
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise RejectedUpload(TOO_LARGE)
                    handle.write(chunk)
                    chunk = stream.read(_CHUNK)
        except BaseException:
            # A refused or interrupted upload leaves nothing behind to be served
            # later by a row that was never written.
            path.unlink(missing_ok=True)
            raise
        return StoredFile(stored_name=stored_name, kind=kind, size=size)

    def path(self, stored_name: str) -> Path:
        """Where a stored name lives, refusing anything that is not one.

        Both separators are rejected on every platform, not just the one this is
        running on: a backslash is an ordinary character in a POSIX filename, so
        ``Path().name`` would hand back ``x\\y.pdf`` intact there and the check
        would quietly hold on Windows only.
        """
        if (
            stored_name in {"", ".", ".."}
            or "/" in stored_name
            or "\\" in stored_name
            or stored_name != Path(stored_name).name
        ):
            raise ValueError(f"{stored_name!r} is not a stored file name.")
        return self.root / stored_name

    def delete(self, stored_name: str) -> None:
        self.path(stored_name).unlink(missing_ok=True)
