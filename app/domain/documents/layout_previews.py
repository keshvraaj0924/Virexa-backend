"""Small, strict PNG envelope validator; no native renderer runs in the API process.

Accept only the RGB/RGBA, 8-bit, noninterlaced PNGs our isolated renderer emits.
Chunk CRCs, dimensions and exact bounded decompression are verified. Ancillary
chunks, concatenated images, trailing bytes and decompression bombs are rejected.
"""
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PREVIEW_BYTES = 1024 * 1024


def validate_preview(image: bytes) -> tuple[int, int]:
    if not isinstance(image, bytes) or not image.startswith(PNG_SIGNATURE):
        raise ValueError("Invalid preview")
    if not 45 <= len(image) <= MAX_PREVIEW_BYTES:
        raise ValueError("Invalid preview size")
    offset, width, height, channels = 8, 0, 0, 0
    compressed = bytearray()
    seen_header = seen_data = ended = False
    while offset < len(image):
        if offset + 12 > len(image):
            raise ValueError("Truncated preview")
        length = struct.unpack_from(">I", image, offset)[0]
        kind = image[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(image):
            raise ValueError("Invalid preview chunk")
        data = image[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", image, offset + 8 + length)[0]
        if zlib.crc32(kind + data) & 0xffffffff != crc:
            raise ValueError("Preview CRC mismatch")
        if kind == b"IHDR" and not seen_header and offset == 8 and length == 13:
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if (not 1 <= width <= 1000 or not 1 <= height <= 1000 or depth != 8
                    or color not in {2, 6} or compression or filtering or interlace):
                raise ValueError("Unsupported preview geometry")
            channels = 3 if color == 2 else 4
            seen_header = True
        elif kind == b"IDAT" and seen_header and not ended:
            seen_data = True
            compressed.extend(data)
        elif kind == b"IEND" and seen_data and length == 0 and end == len(image):
            ended = True
        else:
            raise ValueError("Unsupported preview chunk")
        offset = end
    if not ended:
        raise ValueError("Missing preview end")
    stride = width * channels + 1
    expected = height * stride
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(compressed, expected + 1)
    except zlib.error:
        raise ValueError("Invalid preview compression") from None
    if (len(pixels) != expected or not decoder.eof or decoder.unconsumed_tail
            or decoder.unused_data or any(pixels[i] > 4 for i in range(0, expected, stride))):
        raise ValueError("Invalid preview pixels")
    return width, height
