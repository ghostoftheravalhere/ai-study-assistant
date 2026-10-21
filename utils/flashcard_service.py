"""Generated-card validation and document-aware Gemini orchestration."""

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from utils.config import EMBEDDING_MODEL_NAME
from utils.gemini_service import GeminiService, select_document_context
from utils.indexing_service import list_active_chunks
from utils.retrieval_service import RetrievalError, RetrievalService


MIN_FLASHCARDS = 5
MAX_FLASHCARDS = 30
MAX_FRONT_CHARS = 500
MAX_BACK_CHARS = 5_000
MAX_EXPLANATION_CHARS = 3_000


class FlashcardValidationError(ValueError):
    """Raised when generated flashcard proposals fail validation."""


def validate_generated_flashcard(
    card: Any,
    *,
    source_document_id: str | None,
    allowed_source_pages: set[int],
) -> dict[str, Any]:
    if not isinstance(card, Mapping):
        raise FlashcardValidationError("A generated flashcard was not an object.")
    front, back = card.get("front"), card.get("back")
    explanation = card.get("explanation", "")
    if not isinstance(front, str) or not front.strip() or len(front.strip()) > MAX_FRONT_CHARS:
        raise FlashcardValidationError("A generated card had missing or oversized front text.")
    if not isinstance(back, str) or not back.strip() or len(back.strip()) > MAX_BACK_CHARS:
        raise FlashcardValidationError("A generated card had missing or oversized back text.")
    if not isinstance(explanation, str) or len(explanation.strip()) > MAX_EXPLANATION_CHARS:
        raise FlashcardValidationError("A generated card had invalid explanation text.")
    topic_value = card.get("topic")
    topic = topic_value.strip() if isinstance(topic_value, str) else ""
    if len(topic) > 120 or topic.casefold() in {"unknown", "n/a", "not available"}:
        topic = ""
    raw_pages = card.get("source_pages", [])
    if not isinstance(raw_pages, list):
        raw_pages = []
    pages = sorted({
        page for page in raw_pages
        if isinstance(page, int) and not isinstance(page, bool) and page > 0 and page in allowed_source_pages
    })
    return {
        "card_id": uuid.uuid4().hex,
        "front": front.strip(),
        "back": back.strip(),
        "explanation": explanation.strip(),
        "topic": topic or None,
        "source_document_id": source_document_id,
        "source_pages": pages,
    }


def parse_generated_flashcards(
    raw_response: str,
    *,
    requested_count: int,
    source_document_id: str | None,
    allowed_source_pages: set[int],
) -> list[dict[str, Any]]:
    if not MIN_FLASHCARDS <= requested_count <= MAX_FLASHCARDS:
        raise FlashcardValidationError("Choose between 5 and 30 flashcards.")
    raw = raw_response.strip() if isinstance(raw_response, str) else ""
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, re.I | re.S)
    if fenced:
        raw = fenced.group(1)
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise FlashcardValidationError("Gemini returned unreadable flashcards. Try again.") from error
    raw_cards = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(raw_cards, list):
        raise FlashcardValidationError("Gemini did not return a valid flashcard set. Try again.")
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_card in raw_cards:
        try:
            card = validate_generated_flashcard(
                raw_card, source_document_id=source_document_id,
                allowed_source_pages=allowed_source_pages,
            )
        except FlashcardValidationError:
            continue
        identity = " ".join(card["front"].casefold().split())
        if identity in seen:
            continue
        seen.add(identity)
        cards.append(card)
        if len(cards) == requested_count:
            break
    if len(cards) < MIN_FLASHCARDS:
        raise FlashcardValidationError(
            f"Only {len(cards)} valid, distinct cards were generated. At least {MIN_FLASHCARDS} are needed; reduce the request or choose another source."
        )
    return cards


@dataclass(frozen=True)
class GeneratedFlashcards:
    cards: list[dict[str, Any]]
    requested_count: int
    context_source: str
    context_truncated: bool
    original_context_length: int


def _pages_from_text(text: str) -> set[int]:
    markers = re.findall(r"---\s*Page\s+(\d+)\s*---", text)
    retrieval_pages = re.findall(r'["\']page_number["\']\s*:\s*(\d+)', text)
    return {int(value) for value in (*markers, *retrieval_pages)}


def generate_document_flashcards(
    document: Mapping[str, Any],
    *,
    count: int,
    complexity: str,
    style: str,
    topic_focus: str = "",
    gemini_service: GeminiService,
) -> GeneratedFlashcards:
    document_id = str(document["id"])
    document_text = str(document.get("extracted_text", ""))
    if not document_text.strip():
        raise ValueError("This PDF has no selectable study text.")
    context = ""
    pages: set[int] = set()
    context_source = "bounded extracted PDF text"
    try:
        active_chunks = list_active_chunks(EMBEDDING_MODEL_NAME, document_id)
    except sqlite3.Error:
        active_chunks = []
    if active_chunks:
        try:
            retrieved = RetrievalService(model_name=EMBEDDING_MODEL_NAME).search(
                topic_focus or "important concepts, definitions, processes, and comparisons for study",
                document_id=document_id,
                top_k=10,
            )
            if retrieved.passages:
                context = retrieved.context
                pages = {passage.page_number for passage in retrieved.passages}
                context_source = "locally retrieved PDF passages"
        except (RetrievalError, sqlite3.Error):
            context = ""
    selection = select_document_context(context or document_text)
    pages = _pages_from_text(selection.text)
    result = gemini_service.generate_flashcards(
        selection.text,
        count=count,
        complexity=complexity,
        style=style,
        topic_focus=topic_focus,
        source_document_id=document_id,
        allowed_source_pages=pages,
    )
    return GeneratedFlashcards(
        cards=result.cards,
        requested_count=count,
        context_source=context_source,
        context_truncated=selection.truncated or result.context_truncated,
        original_context_length=max(selection.original_length, result.original_context_length),
    )
