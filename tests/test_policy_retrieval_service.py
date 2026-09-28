"""Chunking is pure and offline (no ChromaDB needed); ingest/retrieve exercise the
live ChromaDB container from docker-compose.yml, same assumption test_decision_engine.py
makes about Postgres already being up.
"""

from app.services import policy_retrieval_service as prs


def test_every_bulletin_parses_into_at_least_one_criterion_chunk():
    chunks = prs.load_policy_bulletin_chunks()
    by_policy: dict[str, list[prs.PolicyPassage]] = {}
    for chunk in chunks:
        by_policy.setdefault(chunk.policy_id, []).append(chunk)

    assert set(by_policy) == {"CP-CARD-009", "CP-PT-002", "CP-ONC-014", "CP-BARI-021", "CP-OPHTH-005"}
    for policy_id, policy_chunks in by_policy.items():
        criteria = [c for c in policy_chunks if c.kind == prs.CRITERION]
        assert criteria, f"{policy_id}: no criteria chunked"
        numbers = sorted(c.number for c in criteria)
        assert numbers == list(range(1, len(numbers) + 1)), f"{policy_id}: criteria numbers not 1..N: {numbers}"


def test_crt_d_policy_chunks_all_five_numbered_criteria():
    chunks = prs.load_policy_bulletin_chunks()
    criteria = [c for c in chunks if c.policy_id == "CP-CARD-009" and c.kind == prs.CRITERION]
    assert len(criteria) == 5

    gdmt = next(c for c in criteria if c.number == 4)
    assert "90-day" in gdmt.text
    assert gdmt.chunk_id == "CP-CARD-009#c4"

    # Criterion 3's indented sub-bullets must stay attached to criterion 3, not
    # split out as their own numbered items.
    qrs = next(c for c in criteria if c.number == 3)
    assert "LBBB" in qrs.text
    assert "non-ischemic cardiomyopathy" in qrs.text


def test_ophthalmology_policy_falls_back_to_a_single_unnumbered_criterion():
    # CP-OPHTH-005 has no numbered list at all -- the whole "Exception criteria"
    # section must still become exactly one assessable criterion, not zero.
    chunks = prs.load_policy_bulletin_chunks()
    criteria = [c for c in chunks if c.policy_id == "CP-OPHTH-005" and c.kind == prs.CRITERION]
    assert len(criteria) == 1
    assert criteria[0].number == 1
    assert "30%" in criteria[0].text


def test_named_sections_are_chunked_separately_from_criteria():
    chunks = prs.load_policy_bulletin_chunks()
    cardiac = [c for c in chunks if c.policy_id == "CP-CARD-009"]
    kinds = {c.kind for c in cardiac}
    assert kinds == {prs.CRITERION, prs.DENIAL_REASONS, prs.APPEAL_GUIDANCE}

    denial_reasons = next(c for c in cardiac if c.kind == prs.DENIAL_REASONS)
    assert denial_reasons.chunk_id == "CP-CARD-009#denial-reasons"
    assert denial_reasons.number is None


def test_build_semantic_query_combines_denial_reason_and_clinical_text():
    denial = {"stated_reason": "Insufficient GDMT documentation", "denied_service": {"description": "CRT-D implant"}}
    query = prs.build_semantic_query(denial, "sacubitril/valsartan initiated 2026-05-11")
    assert "GDMT" in query
    assert "CRT-D" in query
    assert "sacubitril" in query


def test_ingest_and_get_criteria_returns_all_criteria_in_order():
    client = prs.get_chroma_client()
    prs.ingest_policy_bulletins(client)

    criteria = prs.get_criteria(client, "CP-BARI-021")
    assert [c.number for c in criteria] == [1, 2, 3, 4]
    assert all(c.kind == prs.CRITERION for c in criteria)
    assert all(c.distance is None for c in criteria)


def test_retrieve_context_is_scoped_to_the_requested_policy():
    client = prs.get_chroma_client()
    prs.ingest_policy_bulletins(client)

    passages = prs.retrieve_context(client, "CP-PT-002", "objective functional outcome measure Oswestry", n_results=3)
    assert passages
    assert all(p.policy_id == "CP-PT-002" for p in passages)
    assert all(p.distance is not None for p in passages)


def test_retrieve_context_ranks_the_matching_criterion_near_the_top():
    client = prs.get_chroma_client()
    prs.ingest_policy_bulletins(client)

    passages = prs.retrieve_context(client, "CP-CARD-009", "guideline-directed medical therapy beta-blocker trial duration", n_results=2)
    assert any(p.chunk_id == "CP-CARD-009#c4" for p in passages)
