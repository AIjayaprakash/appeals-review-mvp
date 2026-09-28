"""Parses .eml files and stands in for the mailbox connector.

MVP1 polls test_data/emails/ instead of a live mailbox -- the Microsoft Graph
connector described in the technical documentation is out of scope for this local
prototype (see docs/appeals-grievances-technical-documentation.pdf section 2/10).
"""

from __future__ import annotations

import email
from dataclasses import dataclass, field
from pathlib import Path

TEST_MAILBOX_DIR = Path(__file__).resolve().parent.parent.parent / "test_data" / "emails"


@dataclass
class Attachment:
    filename: str
    content_type: str
    content: bytes


@dataclass
class ParsedEmail:
    message_id: str
    sender: str
    subject: str
    date: str
    body_text: str
    attachments: list[Attachment] = field(default_factory=list)


def parse_eml(raw_bytes: bytes) -> ParsedEmail:
    msg = email.message_from_bytes(raw_bytes)
    body_text = ""
    attachments: list[Attachment] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                attachments.append(
                    Attachment(
                        filename=part.get_filename() or "attachment",
                        content_type=part.get_content_type(),
                        content=part.get_payload(decode=True) or b"",
                    )
                )
            elif part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                body_text += payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True) or b""
        body_text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")

    return ParsedEmail(
        message_id=msg.get("Message-ID", ""),
        sender=msg.get("From", ""),
        subject=msg.get("Subject", ""),
        date=msg.get("Date", ""),
        body_text=body_text.strip(),
        attachments=attachments,
    )


def list_test_mailbox() -> list[Path]:
    """Stand-in for polling a live mailbox: every .eml file in test_data/emails."""
    return sorted(TEST_MAILBOX_DIR.glob("*.eml"))
