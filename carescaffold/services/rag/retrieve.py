"""RAG retrieve — query embedding + cosine similarity (spec §4.2).

Spec §4.2.4: "retrieve.py embeds the incoming question with
`input_type='query'`, `output_dimension=1024`, and returns the top-1 or
top-2 chunks by cosine similarity."

v2.6 deviation: TF-IDF instead of Voyage AI. The query is vectorized with
the same TfidfVectorizer fit during ingest, then cosine-similarity-scored
against the chunk matrix. The retrieval interface (top-k chunks with
source_file citations) is preserved.

Spec §4.2.5: "Every claim in a generated response must cite the source
filename(s) it drew from." retrieve.py returns source_file with each
result so scaffold.py can include it in the citation list.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from services.rag.ingest import get_chunk_matrix, get_chunks, get_vectorizer

log = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """One retrieved chunk with similarity score + citation identifier."""
    source_file: str
    topic: str
    content: str
    similarity: float


def retrieve(query: str, top_k: int = 2) -> List[RetrievalResult]:
    """Return the top-k chunks most similar to the query.

    Args:
        query: the patient's question (already redacted per Phase 1,
               which is pending — for now we accept the raw input and
               the inbound safety layer is responsible for emergency
               detection; outbound safety layer is responsible for
               scope enforcement).
        top_k: 1 or 2 per spec §4.2.4. Default 2 to give the generator
               more context.

    Returns: list of RetrievalResult sorted by similarity descending.
    """
    if not query or not query.strip():
        return []

    vectorizer = get_vectorizer()
    chunk_matrix = get_chunk_matrix()
    chunks = get_chunks()

    if not chunks:
        return []

    # Embed the query using the fitted vectorizer
    query_vec = vectorizer.transform([query])

    # Cosine similarity (since TfidfVectorizer L2-normalizes by default,
    # cosine similarity is the same as the dot product of the normalized
    # vectors — which is what sklearn.metrics.pairwise.cosine_similarity
    # computes).
    similarities = cosine_similarity(query_vec, chunk_matrix).flatten()

    # Top-k indices, descending
    top_indices = np.argsort(similarities)[::-1][:top_k]

    results: List[RetrievalResult] = []
    for idx in top_indices:
        chunk = chunks[idx]
        results.append(RetrievalResult(
            source_file=chunk["source_file"],
            topic=chunk["topic"],
            content=chunk["content"],
            similarity=float(similarities[idx]),
        ))

    log.debug(
        "Retrieved top-%d for query '%s...': %s",
        top_k,
        query[:50],
        [(r.source_file, round(r.similarity, 3)) for r in results],
    )
    return results
