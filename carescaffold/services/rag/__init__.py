"""RAG package — Retrieval-Augmented Generation for CareScaffold.

Spec §4.2: ingest.py (chunk embedding + storage), retrieve.py (query
embedding + cosine similarity), scaffold.py (retrieve + persona prompt +
LLM generation).

v2.6 deviation: embeddings use scikit-learn TF-IDF + cosine similarity
instead of Voyage AI voyage-4-large. See /config/model_registry.yaml for
the documented trade-off. The retrieval interface (store a chunk, query
by similarity, return top-k with citation identifiers) is preserved.
"""
