"""Private fixed-command subprocess. No HTTP entry point and no download path.

Linux seccomp blocks creation/use of networking sockets and io_uring before any
optional library is imported. Resource limits bound this process, not a whole
multi-tenant host. Deploy under a dedicated unprivileged account with a read-only
model mount; these controls do not claim full filesystem/container isolation.
"""

from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import importlib.metadata
import io
import json
import math
import os
import re
import resource
import sys
from collections import Counter
from pathlib import Path

# -I excludes cwd/PYTHONPATH; load only this fixed, application-owned module directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout_adapter import (  # noqa: E402
    HERON_REVISION,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_PAGES,
    MAX_PREVIEW_BYTES,
    MEMORY_BYTES,
    MODEL_VERSION,
    PARSER_VERSION,
    TABLEFORMER_REVISION,
    TIMEOUT_SECONDS,
    WARNINGS,
    LayoutError,
)


def _landlock(assets: Path, scratch: Path) -> None:
    """Deny filesystem access outside explicit runtime/model/scratch paths.

    Landlock ABI 1+ is required. Filters apply to all descendants. Never grant a
    shared quarantine directory, cwd, home directory, or application config root.
    """

    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    try:
        if os.uname().machine not in {"x86_64", "aarch64"}:
            raise RuntimeError
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        abi = libc.syscall(444, 0, 0, 1)  # landlock_create_ruleset VERSION
        if abi < 1 or libc.prctl(38, 1, 0, 0, 0):
            raise RuntimeError
        handled = (1 << 13) - 1  # ABI 1 filesystem operations
        if abi >= 2:
            handled |= 1 << 13  # REFER
        if abi >= 3:
            handled |= 1 << 14  # TRUNCATE
        ruleset = Ruleset(handled)
        fd = libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0)
        if fd < 0:
            raise RuntimeError
        try:
            # Only Python/runtime installations, never cwd or the service home.
            paths = {
                Path(sys.prefix),
                Path(sys.base_prefix),
                Path(__file__).resolve().parent,
                assets.resolve(),
                Path("/usr/lib"),
                Path("/lib"),
                Path("/lib64"),
                Path("/etc/ld.so.cache"),
                Path("/etc/localtime"),
                Path("/dev/urandom"),
                Path("/dev/null"),
                Path("/proc/cpuinfo"),
                Path("/proc/meminfo"),
                Path("/sys/devices/system/cpu"),
                Path("/proc/self"),
                scratch,
            }
            for path in paths:
                if not path.exists():
                    continue
                access = (1 << 2) | ((1 << 3) if path.is_dir() else 0)
                if path == scratch:
                    access = handled & ~(1 << 0)  # private temp, no executable launch
                elif path == Path("/dev/null"):
                    access |= 1 << 1
                path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                try:
                    rule = PathRule(access, path_fd)
                    if libc.syscall(445, fd, 1, ctypes.byref(rule), 0):
                        raise RuntimeError
                finally:
                    os.close(path_fd)
            if libc.syscall(446, fd, 0):  # landlock_restrict_self
                raise RuntimeError
        finally:
            os.close(fd)
    except (OSError, RuntimeError, AttributeError):
        raise LayoutError("sandbox_unavailable") from None


def _restrict() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise RuntimeError
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
        lib.seccomp_init.argtypes = [ctypes.c_uint32]
        lib.seccomp_init.restype = ctypes.c_void_p
        lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
        lib.seccomp_rule_add.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        lib.seccomp_rule_add.restype = ctypes.c_int

        class ArgCompare(ctypes.Structure):
            _fields_ = [
                ("arg", ctypes.c_uint),
                ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64),
                ("datum_b", ctypes.c_uint64),
            ]

        lib.seccomp_rule_add_array.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.POINTER(ArgCompare),
        ]
        lib.seccomp_rule_add_array.restype = ctypes.c_int
        lib.seccomp_load.argtypes = [ctypes.c_void_p]
        lib.seccomp_load.restype = ctypes.c_int
        lib.seccomp_release.argtypes = [ctypes.c_void_p]
        context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
        if not context:
            raise RuntimeError
        try:
            for name in (
                "fork",
                "vfork",
                "socket",
                "socketpair",
                "connect",
                "bind",
                "listen",
                "accept",
                "accept4",
                "sendto",
                "sendmsg",
                "sendmmsg",
                "recvfrom",
                "recvmsg",
                "recvmmsg",
                "socketcall",
                "io_uring_setup",
                "io_uring_enter",
                "io_uring_register",
            ):
                number = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
                if number >= 0 and lib.seccomp_rule_add(
                    context, 0x00050000 | errno.EPERM, number, 0
                ):
                    raise RuntimeError
            # pthreads share this process's RLIMIT_AS. Deny separate address spaces.
            clone = lib.seccomp_syscall_resolve_name(b"clone")
            if clone >= 0:
                comparison = ArgCompare(0, 7, 0x10000, 0)  # MASKED_EQ: no CLONE_THREAD
                if lib.seccomp_rule_add_array(
                    context, 0x00050000 | errno.EPERM, clone, 1, ctypes.byref(comparison)
                ):
                    raise RuntimeError
            clone3 = lib.seccomp_syscall_resolve_name(b"clone3")
            if clone3 >= 0 and lib.seccomp_rule_add(context, 0x00050000 | errno.ENOSYS, clone3, 0):
                raise RuntimeError
            if lib.seccomp_load(context):
                raise RuntimeError
        finally:
            lib.seccomp_release(context)
    except (OSError, RuntimeError, AttributeError):
        raise LayoutError("sandbox_unavailable") from None


def _assets(path: Path) -> None:
    """Require the reviewed revision lock plus integrity manifest provisioned offline."""
    try:
        if not path.is_absolute() or not path.is_dir():
            raise ValueError
        lock = json.loads((path / "model-lock.json").read_bytes())
        revisions = {item["repo_id"]: item["revision"] for item in lock}
        if revisions != {
            "ds4sd/docling-layout-heron": HERON_REVISION,
            "ds4sd/docling-models": TABLEFORMER_REVISION,
        }:
            raise ValueError
        # The manifest is an operator trust boundary, never generated at request time.
        manifest = json.loads((path / "layout-assets-sha256.json").read_bytes())
        required = {
            "ds4sd--docling-layout-heron/config.json",
            "ds4sd--docling-layout-heron/preprocessor_config.json",
            "ds4sd--docling-layout-heron/model.safetensors",
            "ds4sd--docling-models/model_artifacts/tableformer/accurate/tm_config.json",
            "ds4sd--docling-models/model_artifacts/tableformer/accurate/tableformer_accurate.safetensors",
        }
        if not isinstance(manifest, dict) or not required.issubset(manifest):
            raise ValueError
        root = path.resolve()
        for relative, digest in manifest.items():
            candidate = path / relative
            if not candidate.resolve().is_relative_to(root) or not candidate.is_file():
                raise ValueError
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError
            with candidate.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
                    raise ValueError
    except (OSError, ValueError, TypeError, KeyError):
        raise LayoutError("assets_unavailable") from None


def _parse(data: bytes, assets: Path) -> dict:
    if not 0 < len(data) <= MAX_INPUT_BYTES:
        raise LayoutError("size_limit")
    if not data.startswith(b"%PDF-"):
        raise LayoutError("invalid_pdf")
    _assets(assets)
    try:
        if importlib.metadata.version("docling") != PARSER_VERSION:
            raise LayoutError("parser_unavailable")
        import pypdfium2 as pdfium
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
        from docling.datamodel.layout_model_specs import DOCLING_LAYOUT_HERON
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except (ImportError, importlib.metadata.PackageNotFoundError):
        raise LayoutError("parser_unavailable") from None
    try:
        pdf = pdfium.PdfDocument(data)
        try:
            native_text = []
            page_count = len(pdf)
            if not 1 <= page_count <= MAX_PAGES:
                raise LayoutError("page_limit")
            for index in range(page_count):
                page = pdf[index]
                try:
                    width, height = page.get_size()
                    if not all(math.isfinite(v) and 0 < v <= 2000 for v in (width, height)):
                        raise LayoutError("resource_limit")
                    text_page = page.get_textpage()
                    try:
                        native_text.append(text_page.get_text_range())
                    finally:
                        text_page.close()
                finally:
                    page.close()
        finally:
            pdf.close()
    except LayoutError:
        raise
    except Exception:
        raise LayoutError("invalid_pdf") from None
    if not 1 <= page_count <= MAX_PAGES:
        raise LayoutError("page_limit")
    options = PdfPipelineOptions(artifacts_path=assets)
    options.layout_options.model_spec = DOCLING_LAYOUT_HERON.model_copy(
        update={"revision": HERON_REVISION}
    )
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    options.do_ocr = False
    options.generate_page_images = True
    options.images_scale = 1.0
    options.do_table_structure = True
    options.enable_remote_services = False
    options.allow_external_plugins = False
    options.document_timeout = TIMEOUT_SECONDS
    options.accelerator_options = AcceleratorOptions(num_threads=2, device=AcceleratorDevice.CPU)
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )
    result = converter.convert(
        DocumentStream(name="document.pdf", stream=io.BytesIO(data)),
        max_num_pages=MAX_PAGES,
        max_file_size=MAX_INPUT_BYTES,
    )
    if result.status != ConversionStatus.SUCCESS or len(result.document.pages) != page_count:
        raise LayoutError("parser_failed")
    previews = []
    for page_no in range(1, page_count + 1):
        page = result.document.pages[page_no]
        if page.image is None:
            raise LayoutError("parser_failed")
        image = page.image.pil_image.copy()
        image.thumbnail((1000, 1000))
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        png = buffer.getvalue()
        if len(png) > MAX_PREVIEW_BYTES:
            raise LayoutError("output_limit")
        previews.append(base64.b64encode(png).decode("ascii"))
    canonical = result.document.export_to_dict()
    # Source visual evidence is a separately access-controlled preview, not an
    # embedded data URI or a remotely reachable image in the canonical layout.
    for page in canonical.get("pages", {}).values():
        page.pop("image", None)
    for picture in canonical.get("pictures", []):
        picture.pop("image", None)
    warnings = list(WARNINGS)
    native_tokens = Counter(re.findall(r"[^\W_]+", " ".join(native_text).casefold()))
    layout_text = [item.get("text", "") for item in canonical.get("texts", [])]
    for table in canonical.get("tables", []):
        layout_text.extend(
            cell.get("text", "") for cell in table.get("data", {}).get("table_cells", [])
        )
    layout_tokens = Counter(re.findall(r"[^\W_]+", " ".join(layout_text).casefold()))
    if native_tokens - layout_tokens:
        warnings.append("native_text_coverage_warning")
    # Absence of the warning does not establish identifier or semantic fidelity.
    # Canonical document preserves reading-order tree, references, tables and provenance.
    # No synthetic confidence, extracted clinical facts or semantic interpretation.
    return {
        "canonical": canonical,
        "page_count": page_count,
        "parser_version": PARSER_VERSION,
        "model_version": MODEL_VERSION,
        "warnings": warnings,
        "previews": previews,
    }


def main() -> None:
    output_fd = os.dup(sys.stdout.fileno())
    # Model/library prints must never corrupt protocol or leak content to parent logs.
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
        os.dup2(sink.fileno(), sys.stderr.fileno())
    try:
        _restrict()
        if len(sys.argv) != 3:
            raise LayoutError("parser_unavailable")
        assets = Path(sys.argv[1])
        scratch = Path(sys.argv[2])
        if not scratch.is_absolute() or not scratch.is_dir():
            raise LayoutError("sandbox_unavailable")
        os.environ["HOME"] = str(scratch)
        os.environ["TMPDIR"] = str(scratch)
        os.environ["HF_HOME"] = str(scratch)
        _landlock(assets, scratch)
        result = _parse(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1), assets)
        payload = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
        if len(payload) > MAX_OUTPUT_BYTES:
            raise LayoutError("output_limit")
    except LayoutError as exc:
        payload = json.dumps({"error": exc.code}).encode()
    except MemoryError:
        payload = b'{"error":"resource_limit"}'
    except Exception:
        payload = b'{"error":"parser_failed"}'
    with os.fdopen(output_fd, "wb") as output:
        output.write(payload)


if __name__ == "__main__":
    main()
