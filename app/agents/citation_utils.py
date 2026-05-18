from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable

_MAX_DISPLAY_DOCS = 4
_SECONDARY_DOC_MIN_RELATIVE_MATCH = 0.74
_SECONDARY_DOC_MIN_EVIDENCE = 0.12
_SECONDARY_DOC_MIN_LEXICAL = 0.45
_SECONDARY_DOC_MIN_RELATIVE_EVIDENCE = 0.48
_COMMON_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "cho",
    "co",
    "cua",
    "duoc",
    "for",
    "from",
    "in",
    "is",
    "la",
    "mot",
    "nhung",
    "of",
    "on",
    "or",
    "tai",
    "tai_lieu",
    "the",
    "to",
    "trong",
    "tu",
    "ve",
    "và",
    "được",
    "có",
    "của",
    "là",
    "một",
    "những",
    "tài",
    "trong",
    "từ",
    "về",
}


@dataclass(frozen=True)
class CitationPreview:
    document_id: str
    chunk_index: int
    file_name: str
    relevance_score: float
    cited_content: str
    chunk_count: int = 1


@dataclass(frozen=True)
class CitationCandidate:
    document_id: str
    chunk_index: int
    file_name: str
    cited_content: str
    raw_score: float
    vector_score: float = 0.0
    lexical_score: float = 0.0


@dataclass(frozen=True)
class _ScoredDocument:
    citation: CitationPreview
    match_score: float
    evidence_score: float
    lexical_signal: float
    retrieval_score: float


def collapse_citations(citations: Iterable[CitationPreview]) -> list[CitationPreview]:
    grouped: dict[str, CitationPreview] = {}
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}

    for index, citation in enumerate(citations):
        key = _citation_key(citation.document_id, citation.file_name)
        if key not in first_seen:
            first_seen[key] = index
        counts[key] = counts.get(key, 0) + max(1, citation.chunk_count)

        current = grouped.get(key)
        if current is None or _is_better_candidate(citation, current):
            grouped[key] = citation

    ordered_keys = sorted(
        grouped.keys(),
        key=lambda key: (-grouped[key].relevance_score, first_seen[key], grouped[key].file_name),
    )

    return [replace(grouped[key], chunk_count=counts[key]) for key in ordered_keys]


def build_display_citations(
    citations: Iterable[CitationCandidate | CitationPreview],
    *,
    query: str,
    max_docs: int = _MAX_DISPLAY_DOCS,
) -> list[CitationPreview]:
    candidates = [_as_candidate(citation) for citation in citations]
    if not candidates:
        return []

    query_tokens = _normalize_tokens(query)
    max_raw_score = max(candidate.raw_score for candidate in candidates) or 1.0
    max_vector_score = max(candidate.vector_score for candidate in candidates)
    max_lexical_score = max(candidate.lexical_score for candidate in candidates)

    grouped: dict[str, list[CitationCandidate]] = {}
    for candidate in candidates:
        key = _citation_key(candidate.document_id, candidate.file_name)
        grouped.setdefault(key, []).append(candidate)

    max_chunk_support = max(len(doc_candidates) for doc_candidates in grouped.values()) or 1
    scored_documents = [
        _score_document(
            doc_candidates,
            query_tokens=query_tokens,
            max_raw_score=max_raw_score,
            max_vector_score=max_vector_score,
            max_lexical_score=max_lexical_score,
            max_chunk_support=max_chunk_support,
        )
        for doc_candidates in grouped.values()
    ]

    scored_documents.sort(
        key=lambda item: (
            -item.match_score,
            -item.evidence_score,
            -item.lexical_signal,
            -item.retrieval_score,
            item.citation.file_name,
        )
    )

    selected = _select_display_documents(scored_documents, max_docs=max_docs)
    return [item.citation for item in selected]


def _select_display_documents(
    scored_documents: list[_ScoredDocument],
    *,
    max_docs: int,
) -> list[_ScoredDocument]:
    if not scored_documents:
        return []

    top = scored_documents[0]
    selected: list[_ScoredDocument] = [top]
    top_match = max(top.match_score, 1e-6)
    top_evidence = max(top.evidence_score, 1e-6)

    for candidate in scored_documents[1:]:
        relative_match = candidate.match_score / top_match
        relative_evidence = candidate.evidence_score / top_evidence
        has_support = candidate.citation.chunk_count >= 2 and candidate.evidence_score >= 0.10
        has_strong_signal = (
            candidate.evidence_score >= _SECONDARY_DOC_MIN_EVIDENCE
            and candidate.lexical_signal >= _SECONDARY_DOC_MIN_LEXICAL
        )

        if relative_match < _SECONDARY_DOC_MIN_RELATIVE_MATCH:
            continue

        if relative_evidence < _SECONDARY_DOC_MIN_RELATIVE_EVIDENCE and not has_support:
            continue

        if not has_strong_signal and not has_support:
            continue

        selected.append(candidate)
        if len(selected) >= max_docs:
            break

    return selected


def collapse_stored_citations(raw_citations: Iterable[Any]) -> list[CitationPreview]:
    return collapse_citations(
        [
            CitationPreview(
                document_id=c.document_id,
                chunk_index=c.chunk_index or 0,
                file_name=c.document_name,
                relevance_score=float(c.score),
                cited_content=c.chunk_text,
                chunk_count=max(1, int(getattr(c, "chunk_count", 1) or 1)),
            )
            for c in raw_citations
        ]
    )


def citation_preview_to_dict(citation: CitationPreview) -> dict[str, object]:
    return asdict(citation)


def _as_candidate(citation: CitationCandidate | CitationPreview) -> CitationCandidate:
    if isinstance(citation, CitationCandidate):
        return citation

    return CitationCandidate(
        document_id=citation.document_id,
        chunk_index=citation.chunk_index,
        file_name=citation.file_name,
        cited_content=citation.cited_content,
        raw_score=float(citation.relevance_score),
    )


def _citation_key(document_id: str, file_name: str) -> str:
    doc_key = (document_id or "").strip()
    name_key = (file_name or "").strip().lower()
    return doc_key or name_key


def _score_document(
    candidates: list[CitationCandidate],
    *,
    query_tokens: set[str],
    max_raw_score: float,
    max_vector_score: float,
    max_lexical_score: float,
    max_chunk_support: int,
) -> _ScoredDocument:
    retrieval_score = _document_retrieval_score(
        candidates,
        max_raw_score=max_raw_score,
        max_vector_score=max_vector_score,
        max_lexical_score=max_lexical_score,
    )
    evidence_score = _document_evidence_score(
        candidates,
        query_tokens=query_tokens,
        max_lexical_score=max_lexical_score,
    )
    lexical_signal = _normalized_signal(
        max(candidate.lexical_score for candidate in candidates),
        max_lexical_score,
    )
    support_score = _document_support_score(candidates, max_chunk_support=max_chunk_support)

    match_score = _clamp(
        (0.45 * retrieval_score) + (0.40 * evidence_score) + (0.15 * support_score),
    )

    best_candidate = _select_best_candidate(
        candidates,
        query_tokens=query_tokens,
        max_raw_score=max_raw_score,
        max_vector_score=max_vector_score,
        max_lexical_score=max_lexical_score,
    )
    preview = CitationPreview(
        document_id=best_candidate.document_id,
        chunk_index=best_candidate.chunk_index,
        file_name=best_candidate.file_name,
        relevance_score=round(match_score, 4),
        cited_content=best_candidate.cited_content[:300],
        chunk_count=len(candidates),
    )
    return _ScoredDocument(
        citation=preview,
        match_score=match_score,
        evidence_score=evidence_score,
        lexical_signal=lexical_signal,
        retrieval_score=retrieval_score,
    )


def _select_best_candidate(
    candidates: list[CitationCandidate],
    *,
    query_tokens: set[str],
    max_raw_score: float,
    max_vector_score: float,
    max_lexical_score: float,
) -> CitationCandidate:
    return max(
        candidates,
        key=lambda candidate: (
            _candidate_quality(
                candidate,
                query_tokens=query_tokens,
                max_raw_score=max_raw_score,
                max_vector_score=max_vector_score,
                max_lexical_score=max_lexical_score,
            ),
            -candidate.chunk_index,
            len(candidate.cited_content),
        ),
    )


def _candidate_quality(
    candidate: CitationCandidate,
    *,
    query_tokens: set[str],
    max_raw_score: float,
    max_vector_score: float,
    max_lexical_score: float,
) -> float:
    retrieval_signal = _chunk_retrieval_score(
        candidate,
        max_raw_score=max_raw_score,
        max_vector_score=max_vector_score,
        max_lexical_score=max_lexical_score,
    )
    evidence_signal = _candidate_evidence_score(
        candidate,
        query_tokens=query_tokens,
        max_lexical_score=max_lexical_score,
    )
    return (0.55 * evidence_signal) + (0.45 * retrieval_signal)


def _document_retrieval_score(
    candidates: list[CitationCandidate],
    *,
    max_raw_score: float,
    max_vector_score: float,
    max_lexical_score: float,
) -> float:
    chunk_scores = sorted(
        (
            _chunk_retrieval_score(
                candidate,
                max_raw_score=max_raw_score,
                max_vector_score=max_vector_score,
                max_lexical_score=max_lexical_score,
            )
            for candidate in candidates
        ),
        reverse=True,
    )
    top_scores = chunk_scores[:2]
    if not top_scores:
        return 0.0
    return sum(top_scores) / len(top_scores)


def _chunk_retrieval_score(
    candidate: CitationCandidate,
    *,
    max_raw_score: float,
    max_vector_score: float,
    max_lexical_score: float,
) -> float:
    raw_signal = _normalized_signal(candidate.raw_score, max_raw_score)
    vector_signal = _normalized_signal(candidate.vector_score, max_vector_score)
    lexical_signal = _normalized_signal(candidate.lexical_score, max_lexical_score)
    return (0.50 * raw_signal) + (0.25 * vector_signal) + (0.25 * lexical_signal)


def _document_evidence_score(
    candidates: list[CitationCandidate],
    *,
    query_tokens: set[str],
    max_lexical_score: float,
) -> float:
    if not query_tokens:
        best_lexical = max(candidate.lexical_score for candidate in candidates)
        return _normalized_signal(best_lexical, max_lexical_score)

    top_candidates = sorted(candidates, key=lambda item: item.raw_score, reverse=True)[:3]
    file_tokens = set().union(*(_normalize_tokens(candidate.file_name) for candidate in candidates))
    content_tokens = set().union(*(_normalize_tokens(candidate.cited_content) for candidate in top_candidates))

    filename_overlap = _token_overlap(query_tokens, file_tokens)
    content_overlap = _token_overlap(query_tokens, content_tokens)
    best_lexical = max(candidate.lexical_score for candidate in candidates)
    lexical_signal = _normalized_signal(best_lexical, max_lexical_score)

    return _clamp((0.45 * content_overlap) + (0.35 * filename_overlap) + (0.20 * lexical_signal))


def _candidate_evidence_score(
    candidate: CitationCandidate,
    *,
    query_tokens: set[str],
    max_lexical_score: float,
) -> float:
    if not query_tokens:
        return _normalized_signal(candidate.lexical_score, max_lexical_score)

    file_tokens = _normalize_tokens(candidate.file_name)
    content_tokens = _normalize_tokens(candidate.cited_content)
    lexical_signal = _normalized_signal(candidate.lexical_score, max_lexical_score)
    return _clamp(
        (0.45 * _token_overlap(query_tokens, content_tokens))
        + (0.35 * _token_overlap(query_tokens, file_tokens))
        + (0.20 * lexical_signal)
    )


def _document_support_score(candidates: list[CitationCandidate], *, max_chunk_support: int) -> float:
    if max_chunk_support <= 1:
        chunk_support = 1.0
    else:
        chunk_support = math.log1p(len(candidates)) / math.log1p(max_chunk_support)

    has_vector = any(candidate.vector_score > 0 for candidate in candidates)
    has_lexical = any(candidate.lexical_score > 0 for candidate in candidates)
    engine_support = (0.5 * float(has_vector)) + (0.5 * float(has_lexical))
    return _clamp((0.70 * chunk_support) + (0.30 * engine_support))


def _is_better_candidate(candidate: CitationPreview, current: CitationPreview) -> bool:
    if candidate.relevance_score != current.relevance_score:
        return candidate.relevance_score > current.relevance_score

    if candidate.chunk_index != current.chunk_index:
        return candidate.chunk_index < current.chunk_index

    return len(candidate.cited_content) > len(current.cited_content)


def _token_overlap(query_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0

    overlap = len(query_tokens & candidate_tokens)
    return overlap / len(query_tokens)


def _normalized_signal(value: float, upper_bound: float) -> float:
    if upper_bound <= 0:
        return 0.0
    return _clamp(value / upper_bound)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalize_tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", (text or "").lower())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[_\-.]+", " ", ascii_text)
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", cleaned)
        if len(token) >= 2 and token not in _COMMON_STOP_WORDS
    }
    return tokens
