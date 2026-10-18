"""Private quarantine only: no public URLs, reads, scanning, parsing or promotion.

This single-host adapter requires a dedicated service-owned directory. Bytes are
never interpreted as code. Header checks are NOT malware scanning or full parsing.
"""

import hashlib
import os
import shutil
import time
from collections.abc import AsyncIterable
from pathlib import Path
from uuid import UUID

import anyio

from app.core.errors import ApiError

SIGNATURES = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


class QuarantineStorage:
    def __init__(self, root: str):
        path = Path(root)
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("Quarantine must be an absolute, non-symlink private directory")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = path.resolve(strict=True)
        info = self.root.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Quarantine must be service-owned with mode 0700")

    def path(self, tenant: UUID, document: UUID, attempt: UUID) -> Path:
        # All components are typed, server-authoritative UUIDs. No client filename.
        return self.root / f"{tenant.hex}-{document.hex}-{attempt.hex}.quarantine"

    def verify(self, path: Path, size: int, checksum: str, media_type: str):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise ApiError(409, "UPLOAD_INCOMPLETE", "Upload bytes are missing.") from None
        with os.fdopen(fd, "rb") as stream:
            if os.fstat(stream.fileno()).st_size != size:
                raise ApiError(409, "UPLOAD_INTEGRITY_FAILED", "Upload integrity check failed.")
            digest = hashlib.sha256()
            header = stream.read(16)
            digest.update(header)
            while chunk := stream.read(65536):
                digest.update(chunk)
            if not header.startswith(SIGNATURES[media_type]) or digest.hexdigest() != checksum:
                raise ApiError(409, "UPLOAD_INTEGRITY_FAILED", "Upload integrity check failed.")

    async def write(
        self, path: Path, chunks: AsyncIterable[bytes], size: int, checksum: str, media_type: str
    ):
        if path.exists():
            # Retry after a successful write but lost DB commit. Never overwrite.
            await anyio.to_thread.run_sync(self.verify, path, size, checksum, media_type)
            return
        # Keep a 1 GiB disk reserve plus room for the four admitted 25 MiB uploads.
        # Single-process adapter; replicas need a shared quota/admission service.
        if shutil.disk_usage(self.root).free < 1024 * 1024 * 1024 + 4 * 26214400:
            raise ApiError(503, "STORAGE_CAPACITY", "Private upload storage is at capacity.")
        temporary = path.with_suffix(".partial")
        # A crashed process can leave a partial. Only recover after twice the
        # entire request deadline; active writes are protected by row/file locks.
        try:
            if time.time() - temporary.lstat().st_mtime > 180:
                temporary.unlink()
        except FileNotFoundError:
            pass
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise ApiError(409, "UPLOAD_BUSY", "An upload is already in progress.") from None
        try:
            count = 0
            digest = hashlib.sha256()
            prefix = b""
            with os.fdopen(fd, "wb") as stream:
                async for chunk in chunks:
                    count += len(chunk)
                    if count > size:
                        raise ApiError(413, "UPLOAD_TOO_LARGE", "Upload exceeds declared size.")
                    prefix = (prefix + chunk[:16])[:16]
                    digest.update(chunk)
                    await anyio.to_thread.run_sync(stream.write, chunk)
                if count != size or digest.hexdigest() != checksum:
                    raise ApiError(409, "UPLOAD_INTEGRITY_FAILED", "Upload integrity check failed.")
                if not prefix.startswith(SIGNATURES[media_type]):
                    raise ApiError(
                        415, "UNSUPPORTED_MEDIA_TYPE", "File signature does not match media type."
                    )
                await anyio.to_thread.run_sync(stream.flush)
                await anyio.to_thread.run_sync(os.fsync, stream.fileno())
            # link is atomic and cannot replace an existing object; both paths are private.
            os.link(temporary, path, follow_symlinks=False)
        finally:
            temporary.unlink(missing_ok=True)
