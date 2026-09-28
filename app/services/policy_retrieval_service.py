"""Chunks reference_data/policy_bulletins/ into ChromaDB and retrieves from it.

Only policy bulletin text goes into the vector store -- denials, eligibility, and
utilization stay exact-match lookups in Postgres (see lookup_service.py and
CLAUDE.md's data rules). Retrieval is always scoped to denial.cited_policy_id first
(an exact filter on chunk metadata); the semantic query built from the case's
clinical evidence only ranks *within* that policy's chunks, it never substitutes
for the exact-match policy selection.

Embeddings use ChromaDB's bundled local MiniLM model (no Azure OpenAI credentials
needed for retrieval -- only the Evaluation Agent's own reasoning, in
app/core/nodes.py, calls Azure OpenAI).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.api import ClientAPI, Collection
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

POLICY_BULLETINS_DIR = Path(__file__).resolve().parent.parent.parent / "reference_data" / "policy_bulletins"
COLLECTION_NAME = "policy_bulletins"

CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))

CRITERION = "criterion"
DENIAL_REASONS = "denial_reasons"
APPEAL_GUIDANCE = "appeal_guidance"

_POLICY_ID_RE = re.compile(r"Clinical Policy Bulletin\s+(CP-[A-Z0-9-]+)")
_SECTION_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)
_NUMBERED_ITEM_RE = re.compile(r"^\d+\.\s+", re.MULTILINE)

_NAMED_SECTIONS = (
    (DENIAL_REASONS, ("reasons for denial",)),
    (APPEAL_GUIDANCE, ("appeal guidance",)),
)


@dataclass
class PolicyPassage:
    """One retrievable unit of a policy bulletin -- a numbered criterion, or the
    denial-reasons / appeal-guidance section. `distance` is only set on semantic
    retrieval results (retrieve_context()); exact-match reads (get_criteria())
    leave it None.
    """

    chunk_id: str
    policy_id: str
    kind: str
    number: int | None
    text: str
    distance: float | None = None


def _split_sections(markdown_text: str) -> list[tuple[str, str]]:
    matches = list(_SECTION_RE.finditer(markdown_text))
    sections = []
    for i, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
        sections.append((heading, markdown_text[start:end].strip()))
    return sections


def _split_numbered_items(content: str) -> list[str]:
    matches = list(_NUMBERED_ITEM_RE.finditer(content))
    items = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        items.append(content[start:end].strip())
    return items


def _criteria_chunks(policy_id: str, sections: list[tuple[str, str]]) -> list[PolicyPassage]:
    numbered_sections = [
        (heading, content, len(_NUMBERED_ITEM_RE.findall(content))) for heading, content in sections
    ]
    numbered_sections = [s for s in numbered_sections if s[2] > 0]
    if numbered_sections:
        _, content, _ = max(numbered_sections, key=lambda s: s[2])
        return [
            PolicyPassage(chunk_id=f"{policy_id}#c{i}", policy_id=policy_id, kind=CRITERION, number=i, text=item)
            for i, item in enumerate(_split_numbered_items(content), start=1)
        ]

    # No numbered list anywhere in the bulletin (e.g. CP-OPHTH-005's single prose
    # exception criterion) -- fall back to the whole criteria/exception section as
    # one unnumbered criterion, rather than silently producing nothing to assess.
    for heading, content in sections:
        if "criteria" in heading.lower() or "exception" in heading.lower():
            return [PolicyPassage(chunk_id=f"{policy_id}#c1", policy_id=policy_id, kind=CRITERION, number=1, text=content)]
    return []


def parse_policy_bulletin(markdown_text: str, source_file: str) -> list[PolicyPassage]:
    policy_match = _POLICY_ID_RE.search(markdown_text)
    if not policy_match:
        raise ValueError(f"{source_file}: no 'Clinical Policy Bulletin CP-...' heading found")
    policy_id = policy_match.group(1)

    sections = _split_sections(markdown_text)
    chunks = _criteria_chunks(policy_id, sections)
    if not chunks:
        raise ValueError(f"{source_file}: could not find any criteria to chunk")

    for kind, keywords in _NAMED_SECTIONS:
        for heading, content in sections:
            if any(keyword in heading.lower() for keyword in keywords):
                chunk_id = f"{policy_id}#{kind.replace('_', '-')}"
                chunks.append(PolicyPassage(chunk_id=chunk_id, policy_id=policy_id, kind=kind, number=None, text=content))
                break

    return chunks


def load_policy_bulletin_chunks() -> list[PolicyPassage]:
    chunks: list[PolicyPassage] = []
    for path in sorted(POLICY_BULLETINS_DIR.glob("*.md")):
        chunks.extend(parse_policy_bulletin(path.read_text(encoding="utf-8"), path.name))
    return chunks


_embedding_function: DefaultEmbeddingFunction | None = None


def _get_embedding_function() -> DefaultEmbeddingFunction:
    global _embedding_function
    if _embedding_function is None:
        _embedding_function = DefaultEmbeddingFunction()
    return _embedding_function


def get_chroma_client() -> ClientAPI:
    return chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)


def get_collection(client: ClientAPI) -> Collection:
    return client.get_or_create_collection(COLLECTION_NAME, embedding_function=_get_embedding_function())


def ingest_policy_bulletins(client: ClientAPI) -> int:
    """Chunks and (re)embeds every policy bulletin. Idempotent -- upsert by
    chunk_id, so re-running after editing a bulletin just updates its chunks.
    """
    collection = get_collection(client)
    chunks = load_policy_bulletin_chunks()
    collection.upsert(
        ids=[c.chunk_id for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[
            {"policy_id": c.policy_id, "kind": c.kind, "number": c.number if c.number is not None else 0}
            for c in chunks
        ],
    )
    return len(chunks)


def _passage_from_get_row(chunk_id: str, text: str, metadata: dict) -> PolicyPassage:
    return PolicyPassage(
        chunk_id=chunk_id,
        policy_id=metadata["policy_id"],
        kind=metadata["kind"],
        number=metadata["number"] or None,
        text=text,
    )


def get_criteria(client: ClientAPI, policy_id: str) -> list[PolicyPassage]:
    """Every numbered criterion for policy_id, in order -- an exact metadata match,
    not semantic search. Check 8 must assess every criterion, so this can never be
    a top-k retrieval; ingest_policy_bulletins() must have run first.
    """
    collection = get_collection(client)
    # Chroma's `where` allows exactly one top-level operator, so two equality
    # conditions must be combined explicitly with $and rather than as sibling keys.
    result = collection.get(where={"$and": [{"policy_id": policy_id}, {"kind": CRITERION}]})
    passages = [
        _passage_from_get_row(chunk_id, text, metadata)
        for chunk_id, text, metadata in zip(result["ids"], result["documents"], result["metadatas"])
    ]
    return sorted(passages, key=lambda p: p.number or 0)


def build_semantic_query(denial: dict, clinical_evidence_text: str) -> str:
    parts = [
        denial.get("stated_reason") or "",
        (denial.get("denied_service") or {}).get("description") or "",
        clinical_evidence_text,
    ]
    return " ".join(p for p in parts if p).strip()


def retrieve_context(client: ClientAPI, policy_id: str, semantic_query: str, n_results: int = 4) -> list[PolicyPassage]:
    """Semantic retrieval of supporting context (denial reasons, appeal guidance,
    and any criteria the query most resembles) for a specific policy -- used to
    ground the Evaluation Agent, in addition to get_criteria()'s exhaustive list.
    """
    collection = get_collection(client)
    result = collection.query(
        query_texts=[semantic_query],
        where={"policy_id": policy_id},
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    passages = []
    for chunk_id, text, metadata, distance in zip(
        result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        passage = _passage_from_get_row(chunk_id, text, metadata)
        passage.distance = distance
        passages.append(passage)
    return passages


def main() -> None:
    client = get_chroma_client()
    count = ingest_policy_bulletins(client)
    print(f"ingested {count} policy bulletin chunks into '{COLLECTION_NAME}'")


if __name__ == "__main__":
    main()
