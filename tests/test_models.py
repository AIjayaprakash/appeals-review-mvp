from app.models import Case, CaseStatus, Channel


def test_case_defaults_to_new_status():
    case = Case(case_id="CASE-0001", channel=Channel.EMAIL)
    assert case.status == CaseStatus.NEW
    assert case.member.member_id is None
    assert case.documents == []
    assert case.audit_trail == []


def test_all_statuses_from_claude_md_present():
    expected = {
        "New",
        "Extracted",
        "Redacted",
        "Structured",
        "Needs Clarification",
        "Summarized",
        "Drafted",
        "Reviewed",
        "Closed",
    }
    assert {s.value for s in CaseStatus} == expected


def test_case_round_trips_through_dict():
    case = Case(
        case_id="CASE-0002",
        channel=Channel.UPLOAD,
        status=CaseStatus.STRUCTURED,
    )
    dumped = case.model_dump(mode="json")
    rebuilt = Case.model_validate(dumped)
    assert rebuilt == case
