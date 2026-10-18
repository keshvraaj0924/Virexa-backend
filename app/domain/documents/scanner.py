"""Local-only ClamAV INSTREAM adapter. Never logs daemon replies or content."""

import asyncio
import os
import stat
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ScanResult:
    verdict: Literal["clean", "rejected", "error"]
    code: str


class ClamAVScanner:
    def __init__(self, socket_path: str, timeout: float = 30, max_age_hours: int = 48):
        if not socket_path.startswith("/") or not 1 <= timeout <= 60:
            raise ValueError("A local absolute Unix socket and bounded timeout are required")
        self.socket_path = socket_path
        self.timeout = timeout
        self.max_age_hours = max_age_hours

    def _trusted_socket(self) -> None:
        path = Path(self.socket_path)
        socket_info = path.lstat()
        if (
            not stat.S_ISSOCK(socket_info.st_mode)
            or socket_info.st_uid not in {0, os.getuid()}
            or socket_info.st_mode & 0o022
        ):
            raise ValueError("Untrusted local scanner socket")
        for directory in path.parents:
            info = directory.lstat()
            # Root-owned sticky /tmp is safe only as an ancestor of a private dir.
            sticky_root = (
                info.st_uid == 0 and info.st_mode & stat.S_ISVTX and directory != path.parent
            )
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid not in {0, os.getuid()}
                or (info.st_mode & 0o022 and not sticky_root)
            ):
                raise ValueError("Untrusted local scanner directory")

    async def _exchange(self, command: bytes, payload: bytes | None = None) -> bytes:
        self._trusted_socket()
        reader, writer = await asyncio.open_unix_connection(self.socket_path, limit=4096)
        try:
            writer.write(command)
            if payload is not None:
                for offset in range(0, len(payload), 65536):
                    chunk = payload[offset : offset + 65536]
                    writer.write(struct.pack("!I", len(chunk)) + chunk)
                    await writer.drain()
                writer.write(b"\0\0\0\0")
            await writer.drain()
            reply = await reader.readuntil(b"\0")
            # A scan has one stream. Additional records must never hide errors.
            if await reader.read(1):
                raise ValueError("Unexpected scanner response")
            return reply[:-1]
        finally:
            writer.close()
            await writer.wait_closed()

    async def scan(self, content: bytes) -> ScanResult:
        if not 0 < len(content) <= 26214400:
            return ScanResult("error", "size_limit")
        try:
            async with asyncio.timeout(self.timeout):
                version = (await self._exchange(b"zVERSION\0")).decode("ascii")
                # VERSION includes the loaded signature database revision and date.
                parts = version.split("/")
                if len(parts) != 3 or not parts[0].startswith("ClamAV ") or int(parts[1]) <= 0:
                    return ScanResult("error", "signatures_unavailable")
                date = datetime.strptime(parts[2], "%a %b %d %H:%M:%S %Y").replace(
                    tzinfo=timezone.utc
                )
                age = (datetime.now(timezone.utc) - date).total_seconds()
                if age < -3600 or age > self.max_age_hours * 3600:
                    return ScanResult("error", "signatures_stale")
                reply = await self._exchange(b"zINSTREAM\0", content)
                if reply == b"stream: OK":
                    return ScanResult("clean", "clean")
                if reply.startswith(b"stream: ") and reply.endswith(b" FOUND"):
                    return ScanResult("rejected", "threat_detected")
                return ScanResult("error", "scanner_error")
        except TimeoutError:
            return ScanResult("error", "scanner_timeout")
        except (
            OSError,
            ValueError,
            UnicodeError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            return ScanResult("error", "scanner_unavailable")
