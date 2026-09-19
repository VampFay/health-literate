"""RAG ingest — embed each markdown file as one chunk (spec §4.2).

Spec §4.2.2: "Each file's entire content is one chunk — do not further
split within a file, and do not merge files into fewer chunks. This gives
you exactly 6 chunks, each well under any embedding model's input
ceiling, each addressing one retrievable concept, and each independently
citable."

Spec §4.2.3: "ingest.py reads each file, embeds it via [embeddings model]
with `input_type='document'`, `output_dimension=1024`, and stores it in
`education_vectors` with the filename as the citation identifier."

v2.6 deviation: embeddings use scikit-learn TfidfVectorizer + cosine
similarity (no API key, no large model download, transparent vocabulary).
See /config/model_registry.yaml. The interface is the same: store a
chunk, retrieve by similarity.

Storage:
- /content/education_module/*.md → row in `education_vectors` table
  (id, source_file, topic, content, embedding_dim)
- Vector representation lives in an in-memory TfidfVectorizer (rebuilt
  on app startup); cosine similarity computed on demand in retrieve.py.
- This is a deliberate simplification for a 6-chunk portfolio demo.
  Production with thousands of chunks would persist vectors to
  `education_vectors_vec` (sqlite-vec vec0 table) or pgvector.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select

from core.db import get_session_factory
from models import EducationVector

log = logging.getLogger(__name__)

CONTENT_DIR = Path(__file__).resolve().parent.parent.parent / "content" / "education_module"

# In-memory TF-IDF state — populated by ingest_all() on app startup.
# Re-populated by tests after table reset. A future production version
# would persist vectors in the DB and load them; for 6 chunks this is
# not necessary and the in-memory approach is more debuggable.
_vectorizer: TfidfVectorizer | None = None
_chunk_matrix = None           # shape: (n_chunks, vocab_size) — the document-term matrix
_chunks: List[Dict] = []       # list of {source_file, topic, content} dicts


async def ingest_all() -> Tuple[int, List[str]]:
    """Read all .md files in /content/education_module/, embed each as one chunk.

    Spec §4.2: each file = one chunk. Files are read in sorted filename order
    so citations are stable.

    Returns (n_chunks, list of source_files).
    """
    global _vectorizer, _chunk_matrix, _chunks

    # Reset state
    _chunks = []
    _vectorizer = None
    _chunk_matrix = None

    if not CONTENT_DIR.exists():
        raise FileNotFoundError(
            f"Education content directory not found: {CONTENT_DIR}. "
            f"Spec §4.2 requires /content/education_module/ with 6 .md files."
        )

    # 1. Load markdown files in sorted order
    md_files = sorted(CONTENT_DIR.glob("*.md"))
    if len(md_files) != 6:
        log.warning(
            "Spec §4.2 expects exactly 6 .md files; found %d. "
            "Proceeding with what's present.", len(md_files)
        )

    file_contents: List[str] = []
    file_metadata: List[Dict] = []
    for md_path in md_files:
        text = md_path.read_text(encoding="utf-8")
        # Strip markdown header (# Diagnosis Basics) for the topic field
        topic = _extract_topic(text, md_path.name)
        file_contents.append(text)
        file_metadata.append({
            "source_file": md_path.name,
            "topic": topic,
            "content": text,
        })

    # 2. Build TF-IDF vectorizer + document-term matrix
    _vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),         # unigrams + bigrams for better matching
        min_df=1,
        max_df=0.95,
        sublinear_tf=True,          # 1 + log(tf) — softens frequent terms
    )
    _chunk_matrix = _vectorizer.fit_transform(file_contents)
    _chunks = file_metadata

    # 3. Persist metadata to the DB (idempotent — upserts by source_file)
    factory = get_session_factory()
    async with factory() as session:
        for meta in _chunks:
            # Check if row exists
            existing = (
                await session.execute(
                    select(EducationVector).where(EducationVector.source_file == meta["source_file"])
                )
            ).scalar_one_or_none()
            if existing:
                existing.topic = meta["topic"]
                existing.content = meta["content"]
                existing.embedding_dim = _chunk_matrix.shape[1]
            else:
                row = EducationVector(
                    source_file=meta["source_file"],
                    topic=meta["topic"],
                    content=meta["content"],
                    embedding_dim=_chunk_matrix.shape[1],
                )
                session.add(row)
        await session.commit()

    log.info(
        "Ingested %d chunks via TF-IDF (vocab=%d dims). "
        "Production would use Voyage AI voyage-4-large (1024-dim) per spec §4.2.",
        len(_chunks),
        _chunk_matrix.shape[1],
    )
    return len(_chunks), [c["source_file"] for c in _chunks]


def _extract_topic(text: str, fallback: str) -> str:
    """Extract the H1 topic from a markdown file, falling back to filename."""
    # Look for "# Title" on the first non-empty line
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line and not line.startswith("#"):
            break
    # Fallback: strip numeric prefix + extension from filename
    return re.sub(r"^\d+_", "", fallback).replace(".md", "").replace("_", " ").title()


def get_chunks() -> List[Dict]:
    """Return the list of ingested chunks (for inspection / testing)."""
    return list(_chunks)


def get_vectorizer() -> TfidfVectorizer:
    """Return the fitted TfidfVectorizer (for retrieve.py and tests)."""
    if _vectorizer is None:
        raise RuntimeError(
            "RAG vectorizer not initialized. Call ingest_all() first."
        )
    return _vectorizer


def get_chunk_matrix():
    """Return the document-term matrix (for retrieve.py)."""
    if _chunk_matrix is None:
        raise RuntimeError(
            "RAG chunk matrix not initialized. Call ingest_all() first."
        )
    return _chunk_matrix
