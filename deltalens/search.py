"""Hybrid search — BM25 full-text + optional dense vector reranking.

Symbol normalization at query time handles module-pattern naming
(e.g., Express-style app.handle → Application.handle).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deltalens.graph import GraphStore
    from deltalens.models import CodeNode

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A search result with relevance score."""

    node: CodeNode
    score: float
    source: str  # "fts" or "embedding" or "hybrid"


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------

# Common abbreviation expansions
_ABBREVIATIONS: dict[str, str] = {
    "req": "request",
    "res": "response",
    "ctx": "context",
    "db": "database",
    "fn": "function",
    "cb": "callback",
    "err": "error",
    "msg": "message",
    "cfg": "config",
    "auth": "authentication",
}

# camelCase / PascalCase → words
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# snake_case → words
_SNAKE_RE = re.compile(r"_+")


def normalize_symbol(name: str) -> list[str]:
    """Normalize a symbol name into searchable tokens.

    Handles:
    - camelCase → ["camel", "case"]
    - snake_case → ["snake", "case"]
    - PascalCase → ["pascal", "case"]
    - Common abbreviations → expanded forms
    - Dot notation → split on dots

    Args:
        name: The symbol name to normalize.

    Returns:
        List of normalized tokens.
    """
    # Split on dots first (module.method)
    parts = name.split(".")

    tokens: list[str] = []
    for part in parts:
        # Split camelCase/PascalCase
        words = _CAMEL_RE.sub("_", part).split("_")
        for word in words:
            word = word.strip().lower()
            if not word:
                continue
            tokens.append(word)
            # Add abbreviation expansion
            if word in _ABBREVIATIONS:
                tokens.append(_ABBREVIATIONS[word])

    return tokens


def build_search_query(query: str) -> str:
    """Build an FTS5-compatible search query with symbol normalization.

    Args:
        query: User's search query.

    Returns:
        FTS5 query string with OR-joined normalized tokens.
    """
    tokens = normalize_symbol(query)
    # Also add the original query terms
    for word in query.split():
        w = word.strip().lower()
        if w and w not in tokens:
            tokens.append(w)

    # FTS5 query: OR-join all tokens with prefix matching
    if not tokens:
        return query + "*"
    return " OR ".join(f"{t}*" for t in tokens)


# ---------------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------------


def search_hybrid(
    store: GraphStore,
    query: str,
    top_k: int = 10,
    use_embeddings: bool = False,
) -> list[SearchResult]:
    """Hybrid search combining BM25 FTS with optional embedding reranking.

    Args:
        store: Graph store with FTS5 index.
        query: User search query.
        top_k: Maximum number of results.
        use_embeddings: Whether to use dense vector reranking.

    Returns:
        Ranked list of SearchResult.
    """
    # Step 1: BM25 search via FTS5
    normalized_query = build_search_query(query)
    fts_results = store.search_fts(normalized_query, limit=top_k * 3)

    results = [
        SearchResult(
            node=node, score=1.0 - (i / len(fts_results)), source="fts"
        )
        for i, node in enumerate(fts_results)
    ]

    # Step 2: Optional embedding reranking
    if use_embeddings and results:
        results = _rerank_with_embeddings(query, results, top_k)

    # Step 3: Deduplicate and limit
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for r in results:
        if r.node.id not in seen:
            seen.add(r.node.id)
            unique.append(r)

    return unique[:top_k]


def _rerank_with_embeddings(
    query: str,
    candidates: list[SearchResult],
    top_k: int,
) -> list[SearchResult]:
    """Rerank candidates using sentence-transformers + cosine similarity.

    Falls back to original ranking if embeddings aren't available.
    """
    try:
        import numpy as np  # type: ignore[import-untyped]
        from sentence_transformers import (
            SentenceTransformer,  # type: ignore[import-untyped]
        )

        model = SentenceTransformer("all-MiniLM-L6-v2")

        # Encode query
        query_embedding = model.encode([query])

        # Encode candidates
        texts = [
            f"{c.node.name} {c.node.signature} {c.node.docstring}"
            for c in candidates
        ]
        candidate_embeddings = model.encode(texts)

        # Cosine similarity
        similarities = np.dot(candidate_embeddings, query_embedding.T).flatten()

        # Combine BM25 and embedding scores (0.6 BM25 + 0.4 embedding)
        for i, cand in enumerate(candidates):
            combined = 0.6 * cand.score + 0.4 * float(similarities[i])
            candidates[i] = SearchResult(
                node=cand.node,
                score=combined,
                source="hybrid",
            )

        candidates.sort(key=lambda r: r.score, reverse=True)
        return candidates[:top_k]

    except ImportError:
        logger.debug(
            "sentence-transformers not installed, skipping embedding reranking"
        )
        return candidates[:top_k]
