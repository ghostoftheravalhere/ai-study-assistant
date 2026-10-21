"""Question-context selection and quiz generation orchestration."""

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from utils.config import EMBEDDING_MODEL_NAME
from utils.gemini_service import GeminiService
from utils.indexing_service import list_active_chunks
from utils.retrieval_service import RetrievalError, RetrievalService


@dataclass(frozen=True)
class GeneratedQuiz:
    questions: list[dict[str, Any]]
    requested_count: int
    context_source: str


_QUIZ_RETRIEVAL_QUERY = "key ideas, important definitions, processes, and relationships suitable for a study quiz"


def _pages_from_extracted_text(text: str) -> set[int]:
    return {int(value) for value in re.findall(r"---\s*Page\s+(\d+)\s*---", text)}


def generate_document_quiz(
    document: Mapping[str, Any],
    *,
    count: int,
    difficulty: str,
    gemini_service: GeminiService,
) -> GeneratedQuiz:
    """Use relevant indexed passages when available, otherwise bounded PDF text."""
    document_id = str(document["id"])
    document_text = str(document.get("extracted_text", ""))
    if not document_text.strip():
        raise ValueError("This document does not contain selectable study text.")

    context = ""
    allowed_pages: set[int] = set()
    context_source = "bounded extracted PDF text"
    if list_active_chunks(EMBEDDING_MODEL_NAME, document_id):
        try:
            retrieval = RetrievalService(model_name=EMBEDDING_MODEL_NAME).search(
                _QUIZ_RETRIEVAL_QUERY,
                document_id=document_id,
                top_k=10,
            )
            if retrieval.passages:
                context = retrieval.context
                allowed_pages = {passage.page_number for passage in retrieval.passages}
                context_source = "locally retrieved PDF passages"
        except RetrievalError:
            # Quiz generation remains available when local semantic retrieval is
            # temporarily unavailable; the fallback is still context-bounded.
            context = ""

    if not context:
        context = document_text
        from utils.gemini_service import select_document_context

        selected = select_document_context(context)
        context = selected.text
        allowed_pages = _pages_from_extracted_text(context)

    questions = gemini_service.generate_quiz_questions(
        context,
        count=count,
        difficulty=difficulty,
        source_document_id=document_id,
        allowed_source_pages=allowed_pages,
    )
    return GeneratedQuiz(questions, count, context_source)
