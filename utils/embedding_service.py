"""Lazy, CPU-only local Sentence Transformers embeddings."""

from typing import Any, Sequence

import numpy as np
import streamlit as st

from utils.config import EMBEDDING_MODEL_NAME

_LOADED_MODEL_NAMES: set[str] = set()


def is_embedding_model_loaded(model_name: str = EMBEDDING_MODEL_NAME) -> bool:
    """Return this process's successful model-load state without initializing it."""
    return model_name in _LOADED_MODEL_NAMES


class EmbeddingModelError(RuntimeError):
    """Raised when the local embedding model cannot be loaded or used."""


@st.cache_resource(show_spinner=False)
def _load_sentence_transformer(model_name: str) -> Any:
    """Load once per Streamlit process, only when an embedding is requested."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise EmbeddingModelError(
            "Local search dependencies are missing. Install the project requirements and restart StudyMate."
        ) from error
    try:
        model = SentenceTransformer(model_name, device="cpu")
        _LOADED_MODEL_NAMES.add(model_name)
        return model
    except (OSError, ValueError, RuntimeError) as error:
        raise EmbeddingModelError(
            "The local embedding model could not be loaded or downloaded. Check your connection and model setting, then try again."
        ) from error


class EmbeddingService:
    """Produce normalized float32 vectors without sending text to an API."""

    def __init__(self, model_name: str | None = None, model_loader: Any | None = None) -> None:
        self.model_name = model_name or EMBEDDING_MODEL_NAME
        self._model_loader = model_loader or _load_sentence_transformer
        self._model: Any | None = None
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            model = self._get_model()
            dimension = model.get_sentence_embedding_dimension()
            if not isinstance(dimension, int) or dimension <= 0:
                raise EmbeddingModelError("The configured embedding model reported an invalid vector dimension.")
            self._dimension = dimension
        return self._dimension

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                self._model = self._model_loader(self.model_name)
            except EmbeddingModelError:
                raise
            except (OSError, ValueError, RuntimeError) as error:
                raise EmbeddingModelError(
                    "The local embedding model could not be loaded or downloaded. Check your connection and model setting, then try again."
                ) from error
        return self._model

    def embed(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        if batch_size < 1:
            raise ValueError("Embedding batch size must be positive.")
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding input must contain non-empty text values.")
        model = self._get_model()
        try:
            vectors = np.asarray(
                model.encode(
                    list(texts),
                    batch_size=batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                ),
                dtype=np.float32,
            )
        except (OSError, ValueError, RuntimeError) as error:
            raise EmbeddingModelError("The local embedding model could not encode this text batch.") from error
        if vectors.ndim == 1 and len(texts) == 1:
            vectors = vectors.reshape(1, -1)
        expected_dimension = self.dimension
        if vectors.ndim != 2 or vectors.shape != (len(texts), expected_dimension):
            raise EmbeddingModelError("The embedding model returned vectors with an unexpected shape or dimension.")
        if not np.isfinite(vectors).all():
            raise EmbeddingModelError("The embedding model returned non-finite vector values.")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if np.any(norms <= 0):
            raise EmbeddingModelError("The embedding model returned an empty vector.")
        vectors /= norms
        return np.ascontiguousarray(vectors, dtype=np.float32)
