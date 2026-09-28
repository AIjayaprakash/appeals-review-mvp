"""PHI/PII tokenization (Presidio) -- tokenize identifiers before any LLM call,
re-hydrate only when assembling the Case for the reviewer (CLAUDE.md hard rule).

Tokens are reversible placeholders (e.g. [[PERSON_1]]), not Presidio's usual
one-way anonymization -- the Intake Agent needs to echo a field's token back
verbatim, and we need to resolve it to the real value afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

ENTITIES = [
    "PERSON",
    "DATE_TIME",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "MEMBER_ID",
    "CASE_REFERENCE",
    "DENIAL_REFERENCE",
]

_MEMBER_ID_RECOGNIZER = PatternRecognizer(
    supported_entity="MEMBER_ID",
    patterns=[Pattern(name="member_id", regex=r"\bMBR-\d{6}\b", score=0.95)],
)
_CASE_REFERENCE_RECOGNIZER = PatternRecognizer(
    supported_entity="CASE_REFERENCE",
    patterns=[Pattern(name="case_reference", regex=r"\bAPL-\d{4}-\d{5}\b", score=0.95)],
)
_DENIAL_REFERENCE_RECOGNIZER = PatternRecognizer(
    supported_entity="DENIAL_REFERENCE",
    patterns=[Pattern(name="denial_reference", regex=r"\bDEN-\d{4}-\d{5}\b", score=0.95)],
)

_analyzer: AnalyzerEngine | None = None


def _get_analyzer() -> AnalyzerEngine:
    global _analyzer
    if _analyzer is None:
        # Presidio defaults to en_core_web_lg (~400MB) if not told otherwise; the
        # small model is more than enough for PERSON/DATE_TIME NER on short intake
        # text and avoids that download.
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }
        ).create_engine()
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        analyzer.registry.add_recognizer(_MEMBER_ID_RECOGNIZER)
        analyzer.registry.add_recognizer(_CASE_REFERENCE_RECOGNIZER)
        analyzer.registry.add_recognizer(_DENIAL_REFERENCE_RECOGNIZER)
        _analyzer = analyzer
    return _analyzer


def _remove_overlaps(results: list[RecognizerResult]) -> list[RecognizerResult]:
    accepted: list[RecognizerResult] = []
    for result in sorted(results, key=lambda r: r.score, reverse=True):
        if not any(result.start < a.end and a.start < result.end for a in accepted):
            accepted.append(result)
    return accepted


@dataclass
class RedactionResult:
    redacted_text: str
    token_map: dict[str, str] = field(default_factory=dict)


def redact(text: str) -> RedactionResult:
    analyzer = _get_analyzer()
    results = _remove_overlaps(analyzer.analyze(text=text, language="en", entities=ENTITIES))
    results_in_reading_order = sorted(results, key=lambda r: r.start)

    token_map: dict[str, str] = {}
    counts: dict[str, int] = {}
    tokens_by_result = []
    for result in results_in_reading_order:
        counts[result.entity_type] = counts.get(result.entity_type, 0) + 1
        token = f"[[{result.entity_type}_{counts[result.entity_type]}]]"
        token_map[token] = text[result.start : result.end]
        tokens_by_result.append((result, token))

    redacted = text
    for result, token in sorted(tokens_by_result, key=lambda rt: rt[0].start, reverse=True):
        redacted = redacted[: result.start] + token + redacted[result.end :]

    return RedactionResult(redacted_text=redacted, token_map=token_map)


def rehydrate(value: str, token_map: dict[str, str]) -> str:
    """Replace any redaction tokens found in `value` with their original text."""
    for token, original in token_map.items():
        value = value.replace(token, original)
    return value
