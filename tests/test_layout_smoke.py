"""Opt-in genuine model + full sandbox smoke. Uses generated synthetic PDFs only.

CI: install pinned extraction requirements (PyTorch CPU index required), provision
assets, then set VIREXA_LAYOUT_SMOKE_ASSETS to that absolute directory. This test
must fail, never skip, when explicitly enabled and sandbox/models are unusable.
It is a functional smoke, not evidence of document/clinical extraction accuracy.
"""

import io
import os
from pathlib import Path

import pytest

from app.domain.documents.layout_adapter import (
    MODEL_VERSION,
    PARSER_VERSION,
    WARNINGS,
    LocalDoclingParser,
)


def synthetic_pdf() -> bytes:
    stream = b"BT /F1 18 Tf 72 720 Td (SYNTHETIC LAYOUT SAMPLE ABC123) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(result)


async def test_genuine_docling_with_full_sandbox():
    assets = os.environ.get("VIREXA_LAYOUT_SMOKE_ASSETS")
    if not assets:
        pytest.skip("Genuine model smoke requires explicit offline assets")
    assert Path(assets).is_absolute()
    # This invokes the production fixed-command, fail-closed subprocess unchanged.
    result = await LocalDoclingParser(assets).parse(synthetic_pdf())
    assert result.page_count == 1
    assert result.parser_version == PARSER_VERSION
    assert result.model_version == MODEL_VERSION
    assert set(WARNINGS).issubset(result.warnings)
    assert len(result.previews) == 1
    from PIL import Image

    with Image.open(io.BytesIO(result.previews[0])) as image:
        assert image.format == "PNG"
        assert max(image.size) <= 1000
    assert all("image" not in page for page in result.canonical["pages"].values())
    text = " ".join(item.get("text", "") for item in result.canonical["texts"])
    assert "SYNTHETIC" in text
    assert result.canonical["body"]["children"]
