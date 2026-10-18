"""Strict PNG validation uses only generated pixels, never customer documents."""
import struct
import zlib

import pytest

from app.domain.documents.layout_previews import validate_preview


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def png(width=1, height=1, raw=b"\0\xff\xff\xff", extra=b""):
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + extra + chunk(b"IEND", b""))


def test_valid_rgb_png():
    assert validate_preview(png()) == (1, 1)


@pytest.mark.parametrize("image", [
    b"\x89PNG\r\n\x1a\ncorrupt-not-image", png() + b"<script>alert(1)</script>",
    png(width=1001), png(width=0), png(raw=b"x" * 1000000), png(raw=b"\x05\xff\xff\xff"),
    png(extra=chunk(b"tEXt", b"untrusted metadata")), png()[:-1],
])
def test_rejects_corrupt_oversized_bomb_or_active_trailer(image):
    with pytest.raises(ValueError):
        validate_preview(image)


def test_crc_mutation_is_rejected():
    image = bytearray(png())
    image[20] ^= 1
    with pytest.raises(ValueError):
        validate_preview(bytes(image))
