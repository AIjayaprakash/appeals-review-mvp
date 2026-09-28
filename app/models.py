"""Case, Denial, Benefits, Utilization schemas.

Mirrors docs/appeals-grievances-technical-documentation.pdf section 8.1 (Case Schema)
and the case statuses listed in CLAUDE.md. No agent or LLM-related fields here.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class CaseStatus(str, Enum):
    NEW = "New"
    EXTRACTED = "Extracted"
    REDACTED = "Redacted"
    STRUCTURED = "Structured"
    NEEDS_CLARIFICATION = "Needs Clarification"
    SUMMARIZED = "Summarized"
    DRAFTED = "Drafted"
    REVIEWED = "Reviewed"
    CLOSED = "Closed"


class Channel(str, Enum):
    EMAIL = "email"
    UPLOAD = "upload"


class Member(BaseModel):
    member_id: str | None = None
    name: str | None = None
    date_of_birth: date | None = None


class Provider(BaseModel):
    name: str | None = None
    npi: str | None = None
    organization: str | None = None


class Request(BaseModel):
    procedure: str | None = None
    cpt_code: str | None = None
    denial_reference: str | None = None
    # The appeal's own tracking number (e.g. APL-2026-00312) -- distinct from
    # denial_reference, and needed so a later duplicate submission can be matched
    # against an already-open case (check 2 / Scenario 9).
    case_reference: str | None = None


class Document(BaseModel):
    filename: str
    source: str
    storage_path: str
    ocr_text: str | None = None


class Denial(BaseModel):
    denial_reference: str
    stated_reason: str | None = None
    cited_policy_id: str | None = None
    appeal_deadline: date | None = None


class Benefits(BaseModel):
    plan_status: str | None = None
    network_status: str | None = None
    annual_limit: int | None = None
    units_used_ytd: int | None = None


class UtilizationEntry(BaseModel):
    date: date
    type: str
    service: str
    outcome: str


class Recommendation(BaseModel):
    decision: str | None = None
    rationale: str | None = None
    cited_guidelines: list[str] = Field(default_factory=list)


class AuditEntry(BaseModel):
    timestamp: datetime
    actor: str
    action: str


class Case(BaseModel):
    case_id: str
    channel: Channel
    status: CaseStatus = CaseStatus.NEW
    member: Member = Field(default_factory=Member)
    provider: Provider = Field(default_factory=Provider)
    request: Request = Field(default_factory=Request)
    diagnosis_codes: list[str] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    clinical_summary: str | None = None
    denial: Denial | None = None
    benefits: Benefits | None = None
    utilization: list[UtilizationEntry] = Field(default_factory=list)
    recommendation: Recommendation | None = None
    audit_trail: list[AuditEntry] = Field(default_factory=list)
