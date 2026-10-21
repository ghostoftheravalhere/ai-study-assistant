"""Page-preserving, deterministic text chunking for local retrieval."""

import hashlib
import re
from dataclasses import dataclass
from typing import Sequence

from utils.config import CHUNK_OVERLAP_CHARS, CHUNK_SIZE_CHARS


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    document_id: str
    position: int
    page_number: int
    text: str
    content_hash: str


def _split_oversized_unit(unit: str, size: int) -> list[str]:
    words = unit.split()
    pieces: list[str] = []
    current = ""
    for word in words:
        while len(word) > size:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:size])
            word = word[size:]
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > size:
            pieces.append(current)
            current = word
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _units(text: str, size: int) -> list[str]:
    # Sentence and paragraph boundaries are preferred; a long sentence is
    # then split at whitespace, with a hard limit for unusually long tokens.
    candidates = re.split(r"(?<=[.!?])\s+|\n\s*\n|\n", text.strip())
    result: list[str] = []
    for candidate in candidates:
        normalized = " ".join(candidate.split())
        if not normalized:
            continue
        result.extend([normalized] if len(normalized) <= size else _split_oversized_unit(normalized, size))
    return result


def _chunk_page(text: str, size: int, overlap: int) -> list[str]:
    units = _units(text, size)
    chunks: list[str] = []
    start = 0
    while start < len(units):
        end = start
        selected: list[str] = []
        length = 0
        while end < len(units):
            addition = len(units[end]) + (1 if selected else 0)
            if selected and length + addition > size:
                break
            selected.append(units[end])
            length += addition
            end += 1
        chunk = " ".join(selected).strip()
        if chunk and chunk not in chunks:
            chunks.append(chunk)
        if end >= len(units):
            break

        # Carry complete trailing sentence units into the next chunk while
        # keeping overlap bounded. A single long unit is never duplicated.
        overlap_units = 0
        overlap_length = 0
        for unit in reversed(selected):
            candidate_length = overlap_length + len(unit) + (1 if overlap_units else 0)
            if candidate_length > overlap:
                break
            overlap_length = candidate_length
            overlap_units += 1
        start = max(start + 1, end - overlap_units)
    return chunks


def chunk_pages(
    document_id: str,
    pages: Sequence[PageText],
    *,
    chunk_size_chars: int = CHUNK_SIZE_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[DocumentChunk]:
    """Create stable chunks without crossing original PDF page boundaries."""
    if chunk_size_chars < 1:
        raise ValueError("Chunk size must be positive.")
    if not 0 <= overlap_chars < chunk_size_chars:
        raise ValueError("Chunk overlap must be non-negative and smaller than the chunk size.")
    result: list[DocumentChunk] = []
    seen_page_text: set[tuple[int, str]] = set()
    for page in pages:
        if page.page_number < 1:
            continue
        for text in _chunk_page(page.text, chunk_size_chars, overlap_chars):
            identity = (page.page_number, text)
            if identity in seen_page_text:
                continue
            seen_page_text.add(identity)
            position = len(result)
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            stable_id = hashlib.sha256(
                f"{document_id}\0{page.page_number}\0{position}\0{content_hash}".encode("utf-8")
            ).hexdigest()
            result.append(
                DocumentChunk(
                    chunk_id=stable_id,
                    document_id=document_id,
                    position=position,
                    page_number=page.page_number,
                    text=text,
                    content_hash=content_hash,
                )
            )
    return result
