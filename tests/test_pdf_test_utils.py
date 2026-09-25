import pytest

from pdf_test_utils import assert_a4_portrait_pdf


def test_a4_pdf_assertion_rejects_scaled_portrait_page():
    half_scale_page = (
        b"%PDF-1.7\n"
        b"1 0 obj << /Type /Page /MediaBox [0 0 297.6378 420.9449] >> endobj\n"
    )

    with pytest.raises(AssertionError):
        assert_a4_portrait_pdf(half_scale_page)


def test_a4_pdf_assertion_accepts_standard_a4_portrait_page():
    a4_page = (
        b"%PDF-1.7\n"
        b"1 0 obj << /Type /Page /MediaBox [0 0 595.2756 841.8898] >> endobj\n"
    )

    assert_a4_portrait_pdf(a4_page)
