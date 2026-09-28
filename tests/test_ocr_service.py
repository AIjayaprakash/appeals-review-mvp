from pathlib import Path

from app.services.ocr_service import extract_text_from_pdf

MEDICAL_HISTORY_DIR = Path(__file__).resolve().parent.parent / "test_data" / "medical_history"


def test_extract_text_from_typed_pdf_reads_real_content():
    pdf_bytes = (MEDICAL_HISTORY_DIR / "MEDHIST-001_structured_case_record.pdf").read_bytes()
    text = extract_text_from_pdf(pdf_bytes)

    assert "Whitfield" in text
    assert "MBR-778241" in text
    assert "I50.22" in text


def test_extract_text_from_multipage_pdf_covers_both_pages():
    pdf_bytes = (MEDICAL_HISTORY_DIR / "MEDHIST-003_multipage_labs.pdf").read_bytes()
    text = extract_text_from_pdf(pdf_bytes)

    assert "Delgado" in text
    # Per test_data/README.md, the clinical justification and signature are on
    # page 2 -- confirming they're present means both pages were read, not just
    # the first.
    assert "Torres" in text


def test_extract_text_from_narrative_letter_pdf():
    pdf_bytes = (MEDICAL_HISTORY_DIR / "MEDHIST-002_appeal_letter_narrative.pdf").read_bytes()
    text = extract_text_from_pdf(pdf_bytes)

    assert "Oyelaran" in text
    assert "11/02/1971" in text
