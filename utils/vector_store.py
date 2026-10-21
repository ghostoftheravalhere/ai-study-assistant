"""Persistent FAISS CPU cache backed by SQLite embedding records."""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from utils.config import INDEX_DIR


class VectorStoreError(RuntimeError):
    """Raised when the local FAISS index cannot be used or rebuilt."""


@dataclass(frozen=True)
class VectorRecord:
    faiss_id: int
    chunk_id: str
    embedding_model: str
    embedding_dimension: int
    embedding_vector: bytes


@dataclass(frozen=True)
class VectorMatch:
    faiss_id: int
    similarity: float


def _load_faiss() -> Any:
    try:
        import faiss
    except ImportError as error:
        raise VectorStoreError(
            "FAISS CPU is not installed. Install the project requirements before searching documents."
        ) from error
    return faiss


def serialize_vector(vector: np.ndarray, dimension: int) -> bytes:
    array = np.asarray(vector, dtype=np.float32)
    if array.ndim != 1 or array.shape[0] != dimension or not np.isfinite(array).all():
        raise ValueError("Embedding vector has an invalid shape or contains non-finite values.")
    return np.ascontiguousarray(array, dtype=np.float32).tobytes(order="C")


def deserialize_vector(blob: bytes, dimension: int) -> np.ndarray:
    if not isinstance(blob, (bytes, bytearray, memoryview)) or len(blob) != dimension * np.dtype(np.float32).itemsize:
        raise VectorStoreError("A stored embedding has an invalid float32 byte length.")
    vector = np.frombuffer(blob, dtype=np.float32).copy()
    if vector.shape != (dimension,) or not np.isfinite(vector).all():
        raise VectorStoreError("A stored embedding contains invalid values.")
    norm = float(np.linalg.norm(vector))
    if norm <= 0:
        raise VectorStoreError("A stored embedding is an empty vector.")
    return np.ascontiguousarray(vector / norm, dtype=np.float32)


def _cache_paths(model_name: str, scope_key: str, index_dir: Path) -> tuple[Path, Path]:
    model_hash = hashlib.sha256(model_name.encode("utf-8")).hexdigest()[:16]
    scope_hash = hashlib.sha256(scope_key.encode("utf-8")).hexdigest()[:16]
    return index_dir / f"study-{model_hash}-{scope_hash}.faiss", index_dir / f"study-{model_hash}-{scope_hash}.json"


class VectorStore:
    """Search normalized float32 vectors with cosine similarity (inner product)."""

    def __init__(self, index_dir: Path | None = None) -> None:
        self.index_dir = Path(index_dir) if index_dir is not None else INDEX_DIR

    def rebuild(
        self,
        records: Sequence[VectorRecord],
        *,
        model_name: str,
        scope_key: str = "all",
    ) -> int:
        """Rebuild a cache from SQLite records and return its vector count."""
        index_path, metadata_path = _cache_paths(model_name, scope_key, self.index_dir)
        if not records:
            for path in (index_path, metadata_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError as error:
                    raise VectorStoreError("The empty FAISS cache could not be cleared.") from error
            return 0
        for path in (index_path, metadata_path):
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                raise VectorStoreError("The old FAISS cache could not be cleared for rebuilding.") from error
        first = deserialize_vector(records[0].embedding_vector, records[0].embedding_dimension)
        self.search(records, first, model_name=model_name, top_k=1, scope_key=scope_key)
        return len(records)

    def search(
        self,
        records: Sequence[VectorRecord],
        query_vector: np.ndarray,
        *,
        model_name: str,
        top_k: int,
        scope_key: str = "all",
    ) -> list[VectorMatch]:
        if top_k < 1:
            raise ValueError("top_k must be positive.")
        if not records:
            return []
        dimension = records[0].embedding_dimension
        if any(record.embedding_model != model_name for record in records):
            raise VectorStoreError("The search set contains embeddings from an incompatible model.")
        if any(record.embedding_dimension != dimension for record in records):
            raise VectorStoreError("The search set contains incompatible embedding dimensions.")
        query = np.asarray(query_vector, dtype=np.float32)
        if query.ndim == 1:
            query = query.reshape(1, -1)
        if query.shape != (1, dimension) or not np.isfinite(query).all():
            raise VectorStoreError("The query embedding has an invalid shape or dimension.")
        query = np.ascontiguousarray(query, dtype=np.float32)
        query_norm = float(np.linalg.norm(query))
        if query_norm <= 0:
            raise VectorStoreError("The query embedding is empty.")
        query /= query_norm

        faiss = _load_faiss()
        self.index_dir.mkdir(parents=True, exist_ok=True)
        index_path, metadata_path = _cache_paths(model_name, scope_key, self.index_dir)
        expected_ids = [int(record.faiss_id) for record in records]
        manifest = hashlib.sha256()
        for record in records:
            manifest.update(str(record.faiss_id).encode("ascii"))
            manifest.update(b"\0")
            manifest.update(record.chunk_id.encode("ascii"))
            manifest.update(b"\0")
            manifest.update(record.embedding_vector)
        fingerprint = manifest.hexdigest()

        index = None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (
                metadata.get("model") == model_name
                and metadata.get("dimension") == dimension
                and metadata.get("count") == len(records)
                and metadata.get("manifest") == fingerprint
                and metadata.get("faiss_ids") == expected_ids
                and index_path.is_file()
            ):
                candidate = faiss.read_index(str(index_path))
                if candidate.d == dimension and candidate.ntotal == len(records):
                    index = candidate
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError, AttributeError):
            # Corrupt or absent cache files are rebuilt from the SQLite source of truth.
            index = None

        if index is None:
            vectors = np.vstack([deserialize_vector(record.embedding_vector, dimension) for record in records])
            index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))
            index.add_with_ids(vectors, np.asarray(expected_ids, dtype=np.int64))
            temporary_index = index_path.with_suffix(".faiss.tmp")
            temporary_metadata = metadata_path.with_suffix(".json.tmp")
            try:
                faiss.write_index(index, str(temporary_index))
                temporary_metadata.write_text(
                    json.dumps(
                        {
                            "model": model_name,
                            "dimension": dimension,
                            "count": len(records),
                            "manifest": fingerprint,
                            "faiss_ids": expected_ids,
                        },
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                os.replace(temporary_index, index_path)
                os.replace(temporary_metadata, metadata_path)
            except OSError as error:
                temporary_index.unlink(missing_ok=True)
                temporary_metadata.unlink(missing_ok=True)
                raise VectorStoreError("The FAISS cache could not be saved. Search can be retried to rebuild it.") from error

        distances, identifiers = index.search(query, min(top_k, len(records)))
        valid_ids = set(expected_ids)
        matches = [
            VectorMatch(int(identifier), float(score))
            for identifier, score in zip(identifiers[0], distances[0])
            if int(identifier) >= 0 and int(identifier) in valid_ids and np.isfinite(score)
        ]
        return matches
