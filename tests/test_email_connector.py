from app.services.email_connector import list_test_mailbox, parse_eml

EMAILS_DIR = list_test_mailbox()[0].parent


def _read(name: str) -> bytes:
    return (EMAILS_DIR / name).read_bytes()


def test_list_test_mailbox_finds_all_eml_files():
    files = list_test_mailbox()
    names = {f.name for f in files}
    assert "EMAIL-001_typical_with_attachment.eml" in names
    assert "EMAIL-002_minimal_missing_member_id.eml" in names
    assert "EMAIL-003_forwarded_multi_attachment.eml" in names
    assert "EMAIL-004_duplicate_of_email_001.eml" in names


def test_parse_email_001_has_body_and_one_pdf_attachment():
    parsed = parse_eml(_read("EMAIL-001_typical_with_attachment.eml"))

    assert "MBR-778241" in parsed.body_text
    assert "Stonebridge" in parsed.body_text
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].filename == "clinical_summary_MBR778241.pdf"
    assert parsed.attachments[0].content_type == "application/pdf"
    assert parsed.attachments[0].content.startswith(b"%PDF")


def test_parse_email_002_is_plain_text_with_no_attachments():
    parsed = parse_eml(_read("EMAIL-002_minimal_missing_member_id.eml"))

    assert "Marion Carter" in parsed.body_text
    assert parsed.attachments == []


def test_parse_email_003_has_pdf_and_text_attachments_and_forwarded_body():
    parsed = parse_eml(_read("EMAIL-003_forwarded_multi_attachment.eml"))

    # The real sender is in the quoted forwarded block, not the From header.
    assert "Front Desk" in parsed.sender
    assert "Dr. Samuel Torres" in parsed.body_text

    filenames = {a.filename for a in parsed.attachments}
    assert filenames == {"case_packet_MBR441098.pdf", "denial_letter_DEN-2026-20117.txt"}

    txt_attachment = next(a for a in parsed.attachments if a.filename.endswith(".txt"))
    assert txt_attachment.content_type == "text/plain"
    assert b"DEN-2026-20117" in txt_attachment.content
