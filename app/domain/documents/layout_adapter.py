"""Bounded optional local PDF layout adapter; importing it never imports Docling.

The worker must supply verified, scan-passed bytes and an operator-managed assets
root. This adapter is not a malware scanner or a complete filesystem sandbox.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import signal
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PIPELINE_VERSION = "docling-local-v1"
PARSER_VERSION = "2.60.1"
HERON_REVISION = "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"
TABLEFORMER_REVISION = "fc0f2d45e2218ea24bce5045f58a389aed16dc23"
MODEL_VERSION = f"heron:{HERON_REVISION};tableformer:{TABLEFORMER_REVISION}"
MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_PAGES = 20
TIMEOUT_SECONDS = 180
MEMORY_BYTES = 6 * 1024 * 1024 * 1024
MAX_PREVIEW_BYTES = 1024 * 1024
WARNINGS = (
    "ocr_disabled",
    "scanned_and_handwritten_content_unsupported",
    "identifier_fidelity_unverified",
)
OPTIONAL_WARNINGS = {"native_text_coverage_warning"}
ERROR_CODES = frozenset(
    {
        "size_limit",
        "invalid_pdf",
        "page_limit",
        "parser_timeout",
        "output_limit",
        "parser_unavailable",
        "assets_unavailable",
        "sandbox_unavailable",
        "parser_failed",
        "resource_limit",
        "invalid_output",
    }
)


class LayoutError(Exception):
    """Safe machine-readable error. Never includes document or subprocess text."""

    def __init__(self, code: str):
        self.code = code if code in ERROR_CODES else "parser_failed"
        super().__init__(self.code)


@dataclass(frozen=True)
class ParseResult:
    canonical: dict[str, Any]
    page_count: int
    parser_version: str
    model_version: str
    warnings: tuple[str, ...] = ()
    previews: tuple[bytes, ...] = ()


def _decode_result(payload: bytes) -> ParseResult:
    try:
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError
        if "error" in result:
            raise LayoutError(
                result["error"] if isinstance(result["error"], str) else "parser_failed"
            )
        canonical = result["canonical"]
        pages = result["page_count"]
        if (
            not isinstance(canonical, dict)
            or type(pages) is not int
            or not 1 <= pages <= MAX_PAGES
            or result["parser_version"] != PARSER_VERSION
            or result["model_version"] != MODEL_VERSION
            or not isinstance(canonical.get("pages"), dict)
            or len(canonical["pages"]) != pages
            or not isinstance(canonical.get("body"), dict)
            or not isinstance(canonical.get("texts"), list)
            or not isinstance(canonical.get("tables"), list)
            or not isinstance(result["warnings"], list)
            or not all(isinstance(w, str) for w in result["warnings"])
            or not set(WARNINGS).issubset(result["warnings"])
            or not set(result["warnings"]).issubset(set(WARNINGS) | OPTIONAL_WARNINGS)
            or not isinstance(result["previews"], list)
            or len(result["previews"]) != pages
        ):
            raise ValueError
        previews = tuple(base64.b64decode(item, validate=True) for item in result["previews"])
        if any(
            not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > MAX_PREVIEW_BYTES
            for image in previews
        ):
            raise ValueError
        return ParseResult(
            canonical, pages, PARSER_VERSION, MODEL_VERSION, tuple(result["warnings"]), previews
        )
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, binascii.Error):
        raise LayoutError("invalid_output") from None


class LocalDoclingParser:
    """Only the server's explicit asset configuration is accepted, never client paths."""

    def __init__(self, assets_path: str):
        path = Path(assets_path)
        if not path.is_absolute():
            raise ValueError("An absolute operator-managed assets directory is required")
        self.assets_path = str(path)

    async def parse(self, data: bytes) -> ParseResult:
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_INPUT_BYTES:
            raise LayoutError("size_limit")
        if not data.startswith(b"%PDF-"):
            raise LayoutError("invalid_pdf")
        if sys.platform != "linux":
            raise LayoutError("sandbox_unavailable")
        # No inherited credentials, proxies, Python path or user site packages.
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "OMP_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
            "TOKENIZERS_PARALLELISM": "false",
        }
        proc: asyncio.subprocess.Process | None = None
        scratch = tempfile.TemporaryDirectory(prefix="virexa-layout-")
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",
                    str(Path(__file__).with_name("layout_subprocess.py")),
                    self.assets_path,
                    scratch.name,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=env,
                    close_fds=True,
                    start_new_session=True,
                )
                assert proc.stdin is not None and proc.stdout is not None

                async def feed() -> None:
                    try:
                        proc.stdin.write(data)
                        await proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        proc.stdin.close()

                async def collect() -> bytes:
                    chunks: list[bytes] = []
                    total = 0
                    while chunk := await proc.stdout.read(65536):
                        total += len(chunk)
                        if total > MAX_OUTPUT_BYTES:
                            raise LayoutError("output_limit")
                        chunks.append(chunk)
                    return b"".join(chunks)

                # TaskGroup cancels the writer if output overflows, avoiding deadlocks.
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(feed())
                    output = tasks.create_task(collect())
                await proc.wait()
                if proc.returncode != 0:
                    if proc.returncode in {-signal.SIGKILL, -signal.SIGXCPU, -signal.SIGXFSZ}:
                        raise LayoutError("resource_limit")
                    raise LayoutError("parser_failed")
                return _decode_result(output.result())
        except TimeoutError:
            raise LayoutError("parser_timeout") from None
        except OSError:
            raise LayoutError("parser_unavailable") from None
        except ExceptionGroup as group:
            if any(
                isinstance(e, LayoutError) and e.code == "output_limit" for e in group.exceptions
            ):
                raise LayoutError("output_limit") from None
            raise LayoutError("parser_failed") from None
        finally:
            if proc is not None:
                # Also terminate descendants; no parser work survives cancellation/timeout.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
            scratch.cleanup()
