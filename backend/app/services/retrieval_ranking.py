"""Provider-free, deterministic lexical ranking and reciprocal rank fusion.

Chinese uses character/bigram terms rather than claiming dictionary segmentation.
Model numbers retain their punctuation; English words use Unicode normalization.
BM25 scores describe the supplied, permission-filtered corpus, never global data.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

TOKENIZER_VERSION = "nfkc-en-model-han-bigram-v1"
STOP_WORDS = frozenset(
    "a an the is are was were be been to of and or for in on at with from by as "
    "what which when where who how does do did can could should would please tell me about".split()
)
HAN_STOP = frozenset("的了是在和与及吗呢请问")
_WORD = re.compile(r"[a-z0-9]+(?:[-_./][a-z0-9]+)*")
_HAN = re.compile(r"[\u3400-\u9fff]+")


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    terms = [term for term in _WORD.findall(normalized) if term not in STOP_WORDS]
    for segment in _HAN.findall(normalized):
        terms.extend(character for character in segment if character not in HAN_STOP)
        terms.extend(segment[index : index + 2] for index in range(len(segment) - 1))
    return terms


def term_data(text: str) -> tuple[dict[str, int], int]:
    counts = Counter(tokenize(text))
    return dict(counts), sum(counts.values())


def lexical_rank(
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    limit: int = 40,
    *,
    k1: float = 1.2,
    b: float = 0.75,
) -> list[tuple[str, float]]:
    """Positive-score BM25 only; a zero-overlap query never returns filler.

    Candidates contain id/content, or persisted term_frequencies/token_count.
    Equal scores are ordered by id so replay and ablations are reproducible.
    """
    if not 1 <= limit <= 10000 or k1 <= 0 or not 0 <= b <= 1:
        raise ValueError("Invalid lexical ranking parameters")
    query_terms = set(tokenize(query))
    if not candidates or not query_terms:
        return []
    rows = []
    for candidate in candidates:
        counts = candidate.get("term_frequencies")
        if not isinstance(counts, Mapping):
            counts, length = term_data(str(candidate.get("content", "")))
        else:
            length = int(candidate.get("token_count", sum(counts.values())))
        rows.append((str(candidate["id"]), counts, length))
    average_length = sum(row[2] for row in rows) / len(rows) or 1
    document_frequency = {
        term: sum(1 for _, counts, _ in rows if counts.get(term, 0) > 0)
        for term in query_terms
    }
    ranked = []
    for identity, counts, length in rows:
        score = 0.0
        for term in query_terms:
            frequency = counts.get(term, 0)
            if frequency <= 0:
                continue
            df = document_frequency[term]
            inverse_document_frequency = math.log(
                1 + (len(rows) - df + 0.5) / (df + 0.5)
            )
            score += inverse_document_frequency * (
                frequency
                * (k1 + 1)
                / (frequency + k1 * (1 - b + b * length / average_length))
            )
        if score > 0:
            ranked.append((identity, score))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))[:limit]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    limit: int = 40,
    rank_constant: int = 60,
) -> list[tuple[str, float]]:
    if not 1 <= limit <= 10000 or rank_constant < 1:
        raise ValueError("Invalid fusion parameters")
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen = set()
        for rank, identity in enumerate(ranking, start=1):
            if identity in seen:
                continue
            seen.add(identity)
            scores[identity] = scores.get(identity, 0) + 1 / (rank_constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
