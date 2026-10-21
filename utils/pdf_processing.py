"""PDF validation and text extraction using PyMuPDF."""

from dataclasses import dataclass
from typing import NamedTuple

import fitz

from utils.config import OCR_MIN_NATIVE_CHARS


class PDFProcessingError(ValueError):
    """Raised when an uploaded PDF cannot be safely read."""


@dataclass(frozen=True)
class PDFExtraction:
    page_count: int
    extracted_text: str
    extraction_status: str
    page_assessments: tuple["PDFPageAssessment", ...] = ()


class PDFPageText(NamedTuple):
    page_number: int
    text: str


@dataclass(frozen=True)
class PDFPageAssessment:
    page_number: int
    native_text: str
    status: str


def _assess_page(page: fitz.Page, page_number: int, text: str, minimum_native_chars: int) -> PDFPageAssessment:
    if len(text) >= minimum_native_chars:
        status = "native_text"
    else:
        rect = page.rect
        page_area = max(float(rect.width * rect.height), 1.0)
        image_area = 0.0
        try:
            for info in page.get_image_info():
                bbox = fitz.Rect(info.get("bbox", (0, 0, 0, 0))) & rect
                image_area += max(0.0, bbox.width * bbox.height)
        except (RuntimeError, ValueError):
            image_area = page_area if page.get_images() else 0.0
        has_image = bool(page.get_images())
        if has_image and image_area / page_area >= 0.15:
            status = "pending"
        elif not text and not has_image and not page.get_drawings():
            status = "blank"
        else:
            status = "not_needed"
    return PDFPageAssessment(page_number, text, status)


def assess_pdf_pages(file_bytes: bytes, *, minimum_native_chars: int = OCR_MIN_NATIVE_CHARS) -> tuple[int, list[PDFPageAssessment]]:
    """Classify pages cheaply using native text and image coverage only."""
    if not file_bytes or not file_bytes.startswith(b"%PDF-"):
        raise PDFProcessingError("This file does not appear to be a readable PDF.")
    try:
        document = fitz.open(stream=file_bytes, filetype="pdf")
    except (fitz.FileDataError, RuntimeError, ValueError) as error:
        raise PDFProcessingError("The PDF is damaged or unreadable. Please try another file.") from error
    try:
        if document.needs_pass:
            raise PDFProcessingError("Password-protected PDFs are not supported. Remove the password and try again.")
        assessments = []
        for number, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            assessments.append(_assess_page(page, number, text, minimum_native_chars))
        return document.page_count, assessments
    except (fitz.FileDataError, RuntimeError, ValueError) as error:
        if isinstance(error, PDFProcessingError):
            raise
        raise PDFProcessingError("The PDF pages could not be assessed safely.") from error
    finally:
        document.close()


def extract_pdf_text(file_bytes: bytes) -> PDFExtraction:
    """Validate PDF bytes and extract text while preserving page boundaries."""
    if not file_bytes:
        raise PDFProcessingError("The selected file is empty.")
    if not file_bytes.startswith(b"%PDF-"):
        raise PDFProcessingError("This file does not appear to be a PDF. Please choose a valid PDF file.")

    try:
        document = fitz.open(stream=file_bytes, filetype="pdf")
    except (fitz.FileDataError, RuntimeError, ValueError) as error:
        raise PDFProcessingError("The PDF is damaged or unreadable. Please try another file.") from error

    try:
        if document.needs_pass:
            raise PDFProcessingError("Password-protected PDFs are not supported. Remove the password and try again.")
        if document.page_count < 1:
            raise PDFProcessingError("This PDF does not contain any pages.")

        page_text = [page.get_text("text").strip() for page in document]
        page_assessments = tuple(
            _assess_page(page, number, text, OCR_MIN_NATIVE_CHARS)
            for number, (page, text) in enumerate(zip(document, page_text), start=1)
        )
        extracted_text = "\n\n".join(
            f"--- Page {page_number} ---\n{text}"
            for page_number, text in enumerate(page_text, start=1)
            if text
        )
        status = "ready" if extracted_text else "no_text"
        return PDFExtraction(
            page_count=document.page_count,
            extracted_text=extracted_text,
            extraction_status=status,
            page_assessments=page_assessments,
        )
    except (fitz.FileDataError, RuntimeError) as error:
        raise PDFProcessingError("Text could not be extracted from this PDF.") from error
    finally:
        document.close()


def extract_pdf_pages(file_bytes: bytes) -> tuple[int, list[PDFPageText]]:
    """Extract page-level text with its real one-based page number."""
    if not file_bytes:
        raise PDFProcessingError("The selected file is empty.")
    if not file_bytes.startswith(b"%PDF-"):
        raise PDFProcessingError("This file does not appear to be a PDF. Please choose a valid PDF file.")
    try:
        document = fitz.open(stream=file_bytes, filetype="pdf")
    except (fitz.FileDataError, RuntimeError, ValueError) as error:
        raise PDFProcessingError("The PDF is damaged or unreadable. Please try another file.") from error
    try:
        if document.needs_pass:
            raise PDFProcessingError("Password-protected PDFs are not supported. Remove the password and try again.")
        if document.page_count < 1:
            raise PDFProcessingError("This PDF does not contain any pages.")
        return document.page_count, [
            PDFPageText(page_number, page.get_text("text").strip())
            for page_number, page in enumerate(document, start=1)
        ]
    except (fitz.FileDataError, RuntimeError) as error:
        raise PDFProcessingError("Text could not be extracted from this PDF.") from error
    finally:
        document.close()
