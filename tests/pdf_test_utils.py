"""PDF-level assertions shared by print and report preview tests."""

import math
import re
import zlib


_MEDIA_BOX = re.compile(
    rb"/MediaBox\s*\[\s*0(?:\.0+)?\s+0(?:\.0+)?\s+([0-9.]+)\s+([0-9.]+)\s*\]"
)


def assert_a4_portrait_pdf(pdf_bytes: bytes) -> None:
    """Check every exposed page box, including compressed page dictionaries."""
    sources = [pdf_bytes]
    for stream_match in re.finditer(rb"stream\r?\n", pdf_bytes):
        stream_end = pdf_bytes.find(b"endstream", stream_match.end())
        if stream_end < 0:
            continue
        # zlib accepts trailing delimiters; stripping bytes can corrupt its checksum.
        stream = pdf_bytes[stream_match.end() : stream_end]
        try:
            sources.append(zlib.decompress(stream))
        except zlib.error:
            continue

    found = False
    for source in sources:
        for match in _MEDIA_BOX.finditer(source):
            found = True
            width_pt, height_pt = (float(value) for value in match.groups())
            assert math.isclose(width_pt, 595.2756, rel_tol=0, abs_tol=0.5), (
                f"expected A4 width 595.28 pt, got {width_pt:.4f} pt"
            )
            assert math.isclose(height_pt, 841.8898, rel_tol=0, abs_tol=0.5), (
                f"expected A4 height 841.89 pt, got {height_pt:.4f} pt"
            )
            assert math.isclose(width_pt / height_pt, 210 / 297, rel_tol=1e-3)
    if not found:
        raise AssertionError("PDF page must expose a MediaBox")
