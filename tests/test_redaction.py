from app.security.redaction import redact, rehydrate


def test_redact_tokenizes_person_member_id_and_reference_numbers():
    text = (
        "Eleanor Whitfield (Member ID MBR-778241) appealed denial reference "
        "DEN-2026-19811, case reference APL-2026-00312."
    )
    result = redact(text)

    assert "Eleanor Whitfield" not in result.redacted_text
    assert "MBR-778241" not in result.redacted_text
    assert "DEN-2026-19811" not in result.redacted_text
    assert "APL-2026-00312" not in result.redacted_text

    assert result.token_map["[[PERSON_1]]"] == "Eleanor Whitfield"
    assert result.token_map["[[MEMBER_ID_1]]"] == "MBR-778241"
    assert result.token_map["[[DENIAL_REFERENCE_1]]"] == "DEN-2026-19811"
    assert result.token_map["[[CASE_REFERENCE_1]]"] == "APL-2026-00312"


def test_rehydrate_restores_original_values():
    text = "Member MBR-441098, Grace K. Delgado, was denied DEN-2026-20117."
    result = redact(text)

    assert rehydrate(result.redacted_text, result.token_map) == text


def test_rehydrate_only_replaces_known_tokens():
    # A field value with no tokens in it should pass through unchanged.
    assert rehydrate("CRT-D implant (CPT 33249)", {"[[PERSON_1]]": "Someone"}) == "CRT-D implant (CPT 33249)"


def test_redact_multiple_occurrences_of_the_same_entity_type_get_distinct_tokens():
    text = "MBR-778241 and MBR-441098 are both referenced here."
    result = redact(text)

    assert result.token_map["[[MEMBER_ID_1]]"] == "MBR-778241"
    assert result.token_map["[[MEMBER_ID_2]]"] == "MBR-441098"
    assert rehydrate(result.redacted_text, result.token_map) == text
