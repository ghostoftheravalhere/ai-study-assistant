"""PDF validation and text extraction using PyMuPDF."""

from dataclasses import dataclass

import fitz


class PDFProcessingError(ValueError):
    """Raised when an uploaded PDF cannot be safely read."""


@dataclass(frozen=True)
class PDFExtraction:
    page_count: int
    extracted_text: str
    extraction_status: str


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
        )
    except (fitz.FileDataError, RuntimeError) as error:
        raise PDFProcessingError("Text could not be extracted from this PDF.") from error
    finally:
        document.close()
