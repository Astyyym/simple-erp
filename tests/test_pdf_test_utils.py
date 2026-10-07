import pytest
import zlib

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


@pytest.mark.parametrize('last_byte', [0x0A, 0x0D])
def test_compressed_page_dictionary_preserves_checksum_eol_byte(last_byte):
    # A compressed-dictionary unit fixture, not a generated business document.
    dictionary = b'<< /Type /Page /MediaBox [0 0 595.2756 841.8898] >>\n%'
    compressed = next(zlib.compress(dictionary + b'A' * padding)
                      for padding in range(256)
                      if zlib.compress(dictionary + b'A' * padding)[-1] == last_byte)
    assert compressed[-1] == last_byte
    assert b'/MediaBox' in zlib.decompress(compressed)
    fixture = (b'%PDF-1.7\n1 0 obj\n<< /Filter /FlateDecode /Length '
               + str(len(compressed)).encode() + b' >>\nstream\n'
               + compressed + b'\nendstream\nendobj\n%%EOF\n')
    assert_a4_portrait_pdf(fixture)


def test_every_page_box_must_be_a4_not_only_the_first():
    fixture = (b'%PDF-1.7\n1 0 obj << /Type /Page /MediaBox [0 0 595.2756 841.8898] >> endobj\n'
               b'2 0 obj << /Type /Page /MediaBox [0 0 612 792] >> endobj\n%%EOF\n')
    with pytest.raises(AssertionError, match='expected A4'):
        assert_a4_portrait_pdf(fixture)
