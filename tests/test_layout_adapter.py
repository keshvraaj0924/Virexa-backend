"""Synthetic protocol/unit coverage. No real model quality or clinical validation."""

import asyncio
import base64
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.documents.layout_adapter import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    MODEL_VERSION,
    PARSER_VERSION,
    WARNINGS,
    LayoutError,
    LocalDoclingParser,
    _decode_result,
)


def protocol():
    return {
        "canonical": {"pages": {"1": {}}, "body": {"children": []}, "texts": [], "tables": []},
        "page_count": 1,
        "parser_version": PARSER_VERSION,
        "model_version": MODEL_VERSION,
        "warnings": list(WARNINGS),
        "previews": [base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode()],
    }


def test_optional_import_does_not_load_docling():
    code = "import sys; import app.domain.documents.layout_adapter; assert 'docling' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize(
    "payload,code",
    [
        (b"", "size_limit"),
        (b"no", "invalid_pdf"),
        (b"%PDF-" + b"a" * MAX_INPUT_BYTES, "size_limit"),
    ],
)
async def test_rejects_invalid_input_without_subprocess(payload, code):
    with patch("asyncio.create_subprocess_exec") as spawn:
        with pytest.raises(LayoutError, match=code):
            await LocalDoclingParser("/offline/models").parse(payload)
        spawn.assert_not_called()


def test_requires_absolute_server_path():
    with pytest.raises(ValueError):
        LocalDoclingParser("relative")


def test_round_trip_preserves_canonical_order_and_provenance():
    value = protocol()
    value["canonical"]["body"]["children"] = [{"$ref": "#/texts/1"}, {"$ref": "#/texts/0"}]
    value["canonical"]["texts"] = [
        {
            "text": "synthetic",
            "prov": [
                {"page_no": 1, "bbox": {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "TOPLEFT"}}
            ],
        }
    ]
    parsed = _decode_result(json.dumps(value).encode())
    assert parsed.canonical == value["canonical"]
    assert parsed.page_count == 1
    assert parsed.warnings == WARNINGS
    assert "confidence" not in parsed.canonical


@pytest.mark.parametrize(
    "change",
    [
        {"page_count": 21},
        {"page_count": True},
        {"parser_version": "latest"},
        {"model_version": "unknown"},
        {"canonical": {}},
        {"warnings": ["document private data"]},
    ],
)
def test_protocol_rejects_invalid_results(change):
    value = protocol() | change
    with pytest.raises(LayoutError, match="invalid_output"):
        _decode_result(json.dumps(value).encode())


def test_errors_are_sanitized():
    with pytest.raises(LayoutError, match="parser_failed") as error:
        _decode_result(b'{"error":"secret content or path"}')
    assert str(error.value) == "parser_failed"


async def test_real_subprocess_fails_closed_without_assets(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Linux seccomp required")
    # Synthetic bytes only. This checks the genuine subprocess, not model execution.
    with pytest.raises(LayoutError) as error:
        await LocalDoclingParser(str(tmp_path)).parse(b"%PDF-1.4\nsynthetic")
    assert error.value.code in {"assets_unavailable", "sandbox_unavailable"}


def test_real_seccomp_denies_network():
    if sys.platform != "linux":
        pytest.skip("Linux seccomp required")
    module = Path(__file__).resolve().parents[1] / "app/domain/documents"
    code = f"""
import sys, socket, errno, os, threading
sys.path.insert(0, {str(module)!r})
from layout_subprocess import _restrict
from layout_adapter import LayoutError
try:
    _restrict()
except LayoutError:
    sys.exit(77)
try:
    socket.socket(socket.AF_INET, socket.SOCK_STREAM)
except OSError as exc:
    assert exc.errno == errno.EPERM
else:
    raise AssertionError('Network socket unexpectedly allowed')
completed = []
thread = threading.Thread(target=lambda: completed.append(True))
thread.start()
thread.join()
assert completed == [True]
try:
    os.fork()
except OSError as exc:
    assert exc.errno == errno.EPERM
else:
    raise AssertionError('Separate-process fork unexpectedly allowed')
"""
    result = subprocess.run([sys.executable, "-I", "-c", code], timeout=10)
    if result.returncode == 77:
        pytest.skip("Host cannot install seccomp; production parser fails closed")
    assert result.returncode == 0


class FakeProcess:
    """Explicit mock protocol fixture, not Docling output evidence."""

    pid = 987654321
    returncode = 0

    def __init__(self, output):
        self.stdin = AsyncMock()
        self.stdin.write = lambda _: None
        self.stdin.close = lambda: None
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(output)
        self.stdout.feed_eof()
        self.wait = AsyncMock(return_value=0)


async def test_fixed_command_and_clean_environment():
    process = FakeProcess(json.dumps(protocol()).encode())
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
        with patch("os.killpg"):
            result = await LocalDoclingParser("/offline/models").parse(b"%PDF-synthetic")
    assert result.page_count == 1
    args, kwargs = spawn.call_args
    assert args[0] == sys.executable
    assert args[1] == "-I"
    assert args[2].endswith("/layout_subprocess.py")
    assert args[3] == "/offline/models"
    assert len(args) == 5
    assert Path(args[4]).name.startswith("virexa-layout-")
    assert not Path(args[4]).exists()
    assert kwargs["close_fds"] and kwargs["start_new_session"]
    assert "PYTHONPATH" not in kwargs["env"]
    assert "HOME" not in kwargs["env"]
    assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"


async def test_output_cap_kills_process_group():
    process = FakeProcess(b"x" * (MAX_OUTPUT_BYTES + 1))
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        with patch("os.killpg") as kill:
            with pytest.raises(LayoutError, match="output_limit"):
                await LocalDoclingParser("/offline/models").parse(b"%PDF-synthetic")
            kill.assert_called_once()
    process.wait.assert_awaited()


async def test_timeout_kills_process_group():
    process = FakeProcess(b"")

    async def block(*args):
        await asyncio.sleep(10)

    process.stdout.read = block
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        with patch("app.domain.documents.layout_adapter.TIMEOUT_SECONDS", 0.01):
            with patch("os.killpg") as kill:
                with pytest.raises(LayoutError, match="parser_timeout"):
                    await LocalDoclingParser("/offline/models").parse(b"%PDF-synthetic")
                kill.assert_called_once()
    process.wait.assert_awaited()


async def test_cancellation_kills_process_group():
    process = FakeProcess(b"")

    async def block(*args):
        await asyncio.sleep(10)

    process.stdout.read = block
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        with patch("os.killpg") as kill:
            task = asyncio.create_task(
                LocalDoclingParser("/offline/models").parse(b"%PDF-synthetic")
            )
            await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            kill.assert_called_once()
    process.wait.assert_awaited()


def test_real_landlock_denies_same_uid_file_outside_allowlist(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Linux Landlock required")
    assets = tmp_path / "models"
    assets.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    secret = tmp_path / "other-tenant.txt"
    secret.write_text("synthetic neighboring tenant data")
    allowed = assets / "model-test.txt"
    allowed.write_text("synthetic model fixture")
    module = Path(__file__).resolve().parents[1] / "app/domain/documents"
    code = f"""
import sys, errno
from pathlib import Path
sys.path.insert(0, {str(module)!r})
from layout_subprocess import _restrict, _landlock
from layout_adapter import LayoutError
try:
    _restrict()
    _landlock(Path({str(assets)!r}), Path({str(scratch)!r}))
except LayoutError:
    sys.exit(77)
assert Path({str(allowed)!r}).read_text() == 'synthetic model fixture'
Path({str(scratch / "test.txt")!r}).write_text('synthetic scratch')
try:
    Path({str(secret)!r}).read_text()
except OSError as exc:
    assert exc.errno == errno.EACCES
else:
    raise AssertionError('Other-tenant file unexpectedly readable')
try:
    Path({str(allowed)!r}).write_text('mutation')
except OSError as exc:
    assert exc.errno == errno.EACCES
else:
    raise AssertionError('Model unexpectedly writable')
"""
    result = subprocess.run([sys.executable, "-I", "-c", code], timeout=10)
    if result.returncode == 77:
        pytest.skip("Host cannot install Landlock; production parser fails closed")
    assert result.returncode == 0


def test_asset_revision_and_integrity_checks(tmp_path):
    import hashlib

    from app.domain.documents.layout_adapter import HERON_REVISION, TABLEFORMER_REVISION
    from app.domain.documents.layout_subprocess import _assets

    revisions = [
        {"repo_id": "ds4sd/docling-layout-heron", "revision": HERON_REVISION},
        {"repo_id": "ds4sd/docling-models", "revision": TABLEFORMER_REVISION},
    ]
    names = [
        "ds4sd--docling-layout-heron/config.json",
        "ds4sd--docling-layout-heron/preprocessor_config.json",
        "ds4sd--docling-layout-heron/model.safetensors",
        "ds4sd--docling-models/model_artifacts/tableformer/accurate/tm_config.json",
        "ds4sd--docling-models/model_artifacts/tableformer/accurate/tableformer_accurate.safetensors",
    ]
    manifest = {}
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic model integrity test, not real weights")
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "model-lock.json").write_text(json.dumps(revisions))
    (tmp_path / "layout-assets-sha256.json").write_text(json.dumps(manifest))
    _assets(tmp_path)
    (tmp_path / names[0]).write_bytes(b"tampered synthetic fixture")
    with pytest.raises(Exception, match="assets_unavailable"):
        _assets(tmp_path)
    revisions[0]["revision"] = "main"
    (tmp_path / "model-lock.json").write_text(json.dumps(revisions))
    with pytest.raises(Exception, match="assets_unavailable"):
        _assets(tmp_path)


def test_preview_validation_and_optional_coverage_warning():
    value = protocol()
    value["warnings"].append("native_text_coverage_warning")
    parsed = _decode_result(json.dumps(value).encode())
    assert "native_text_coverage_warning" in parsed.warnings
    value["previews"] = [base64.b64encode(b"not a PNG").decode()]
    with pytest.raises(LayoutError, match="invalid_output"):
        _decode_result(json.dumps(value).encode())
    value["previews"] = []
    with pytest.raises(LayoutError, match="invalid_output"):
        _decode_result(json.dumps(value).encode())
