"""POST /intake/upload, POST /intake/email/poll.

Orchestrates ingestion -> redaction -> intake_agent -> re-hydration -> Case
construction and persistence. Phase 4's compiled LangGraph (app/core/graph.py) will
replace this manual pipeline; for now both endpoints run the same sequence directly.

test_data/emails/ stands in for a live mailbox -- see app/services/email_connector.py.
Each poll reprocesses every .eml file and opens a new case per message; a real
appeal referencing an already-open case is exactly what check 2 (Scenario 9,
duplicate/merge) exists to catch downstream, not something intake itself dedupes.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, UploadFile

from app import db
from app.core.nodes import intake_agent
from app.models import Case, CaseStatus, Channel, Document, Member, Provider, Request
from app.security.redaction import redact, rehydrate
from app.services import email_connector, lookup_service, ocr_service

router = APIRouter(prefix="/intake", tags=["intake"])


def _new_case_id() -> str:
    return f"CASE-{uuid.uuid4().hex[:8].upper()}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _rehydrate_extraction(extraction: dict, token_map: dict[str, str]) -> dict:
    rehydrated: dict = {}
    for key, value in extraction.items():
        if isinstance(value, str):
            rehydrated[key] = rehydrate(value, token_map)
        elif isinstance(value, list):
            rehydrated[key] = [rehydrate(v, token_map) if isinstance(v, str) else v for v in value]
        else:
            rehydrated[key] = value
    return rehydrated


def _is_pdf(filename: str, content_type: str | None) -> bool:
    return (content_type or "") == "application/pdf" or filename.lower().endswith(".pdf")


def _extract_document_text(filename: str, content_type: str | None, content: bytes) -> str:
    if _is_pdf(filename, content_type):
        return ocr_service.extract_text_from_pdf(content)
    return content.decode("utf-8", errors="replace")


def build_case(case_id: str, channel: Channel, working_text: str, documents: list[Document]) -> Case:
    redaction_result = redact(working_text)
    extraction = intake_agent(redaction_result.redacted_text)
    fields = _rehydrate_extraction(extraction, redaction_result.token_map)
    date_of_birth = _parse_date(fields["date_of_birth"])

    status = (
        CaseStatus.STRUCTURED
        if lookup_service.has_sufficient_identity(fields["member_id"], fields["member_name"], date_of_birth)
        else CaseStatus.NEEDS_CLARIFICATION
    )

    return Case(
        case_id=case_id,
        channel=channel,
        status=status,
        member=Member(member_id=fields["member_id"], name=fields["member_name"], date_of_birth=date_of_birth),
        provider=Provider(name=fields["requesting_provider"], organization=fields["sender_org"]),
        request=Request(
            procedure=fields["procedure_requested"],
            denial_reference=fields["denial_reference"],
            case_reference=fields["case_reference"],
        ),
        diagnosis_codes=fields["diagnosis_codes"],
        documents=documents,
    )


@router.post("/upload", response_model=Case)
async def upload_case(file: UploadFile) -> Case:
    content = await file.read()
    filename = file.filename or "upload"
    text = _extract_document_text(filename, file.content_type, content)

    case_id = _new_case_id()
    document = Document(
        filename=filename,
        source="upload",
        storage_path=f"uploads/{case_id}/{file.filename}",
        ocr_text=text,
    )
    case = build_case(case_id, Channel.UPLOAD, text, [document])

    conn = db.get_connection()
    try:
        db.create_case(conn, case)
    finally:
        conn.close()
    return case


@router.post("/email/poll", response_model=list[Case])
def poll_email() -> list[Case]:
    cases: list[Case] = []
    conn = db.get_connection()
    try:
        for eml_path in email_connector.list_test_mailbox():
            parsed = email_connector.parse_eml(eml_path.read_bytes())

            documents: list[Document] = []
            working_text_parts = [parsed.body_text]
            for attachment in parsed.attachments:
                attachment_text = _extract_document_text(
                    attachment.filename, attachment.content_type, attachment.content
                )
                working_text_parts.append(attachment_text)
                documents.append(
                    Document(
                        filename=attachment.filename,
                        source="email",
                        storage_path=f"emails/{eml_path.name}/{attachment.filename}",
                        ocr_text=attachment_text,
                    )
                )

            case_id = _new_case_id()
            case = build_case(case_id, Channel.EMAIL, "\n\n".join(working_text_parts), documents)
            db.create_case(conn, case)
            cases.append(case)
    finally:
        conn.close()
    return cases
