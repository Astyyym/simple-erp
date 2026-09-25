"""PDF-level assertions shared by print and report preview tests."""

import math
import re
import zlib


_MEDIA_BOX = re.compile(
    rb"/MediaBox\s*\[\s*0(?:\.0+)?\s+0(?:\.0+)?\s+([0-9.]+)\s+([0-9.]+)\s*\]"
)


def assert_a4_portrait_pdf(pdf_bytes: bytes) -> None:
    """Assert the generated PDF page box is A4 portrait, including compressed dictionaries."""
    sources = [pdf_bytes]
    for stream_match in re.finditer(rb"stream\r?\n", pdf_bytes):
        stream_end = pdf_bytes.find(b"endstream", stream_match.end())
        if stream_end < 0:
            continue
        stream = pdf_bytes[stream_match.end() : stream_end].rstrip(b"\r\n")
        try:
            sources.append(zlib.decompress(stream))
        except zlib.error:
            continue

    for source in sources:
        match = _MEDIA_BOX.search(source)
        if match is not None:
            width_pt, height_pt = (float(value) for value in match.groups())
            assert math.isclose(width_pt, 595.2756, rel_tol=0, abs_tol=0.5), (
                f"expected A4 width 595.28 pt, got {width_pt:.4f} pt"
            )
            assert math.isclose(height_pt, 841.8898, rel_tol=0, abs_tol=0.5), (
                f"expected A4 height 841.89 pt, got {height_pt:.4f} pt"
            )
            assert math.isclose(width_pt / height_pt, 210 / 297, rel_tol=1e-3)
            return

    raise AssertionError("PDF page must expose a MediaBox")
