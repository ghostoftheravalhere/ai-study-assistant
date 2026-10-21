"""Explicit, local-only Tesseract OCR for selected PDF pages."""

import io
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

import fitz

from utils.config import OCR_DPI, OCR_LANGUAGE, OCR_MIN_NATIVE_CHARS, TESSERACT_CMD
from utils.database import get_connection
from utils.documents import DocumentError, get_document, resolve_stored_document_path
from utils.pdf_processing import PDFProcessingError, assess_pdf_pages


class OCRError(RuntimeError):
    """Safe, user-readable local OCR error."""


class OCREngine(Protocol):
    def languages(self) -> set[str]: ...
    def recognize(self, image: object, language: str, timeout: int) -> str: ...
    def version(self) -> str: ...


class TesseractEngine:
    def __init__(self) -> None:
        try:
            import pytesseract
        except ImportError as error:
            raise OCRError("The local OCR integration is missing. Install the project requirements, then restart StudyMate.") from error
        self._api = pytesseract
        if TESSERACT_CMD:
            self._api.pytesseract.tesseract_cmd = TESSERACT_CMD

    def languages(self) -> set[str]:
        try:
            return set(self._api.get_languages(config=""))
        except self._api.TesseractNotFoundError as error:
            raise OCRError("Tesseract was not found. Install Tesseract or set STUDY_ASSISTANT_TESSERACT_CMD to its executable path.") from error
        except self._api.TesseractError as error:
            raise OCRError("Tesseract could not read its language data. Install the selected language pack and check the Tesseract tessdata configuration.") from error

    def recognize(self, image: object, language: str, timeout: int) -> str:
        try:
            return self._api.image_to_string(image, lang=language, config="--psm 3", timeout=timeout)
        except self._api.TesseractNotFoundError as error:
            raise OCRError("Tesseract was not found. Install it or configure STUDY_ASSISTANT_TESSERACT_CMD.") from error
        except self._api.TesseractError as error:
            message = str(error).casefold()
            if "failed loading language" in message or "could not initialize" in message or "error opening data file" in message:
                raise OCRError(f"Tesseract language data for '{language}' is missing. Install the requested language pack.") from error
            raise OCRError("Tesseract could not recognize this page. You can retry OCR for this document.") from error
        except subprocess.SubprocessError as error:
            raise OCRError("The local OCR process could not complete. You can retry this page.") from error
        except RuntimeError as error:
            if "timeout" in str(error).casefold():
                raise OCRError("OCR timed out on this page. Retry the document or use a lower OCR resolution.") from error
            raise OCRError("OCR could not process this page. You can retry OCR for this document.") from error

    def version(self) -> str:
        try:
            return str(self._api.get_tesseract_version())[:80]
        except Exception:
            return "unknown"


@dataclass(frozen=True)
class OCRRunResult:
    document_id: str
    processed: int
    succeeded: int
    empty: int
    failed: int
    remaining: int
    errors: tuple[str, ...] = ()


def _render_page(page: fitz.Page, dpi: int):
    from PIL import Image

    rect = page.rect
    scale = dpi / 72
    estimated_pixels = max(rect.width * scale, 1) * max(rect.height * scale, 1)
    if estimated_pixels > 25_000_000:
        scale *= (25_000_000 / estimated_pixels) ** 0.5
    scale = min(scale, 300 / 72)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, annots=False)
    if pixmap.width * pixmap.height > 25_000_000:
        raise OCRError("This PDF page is too large to render safely for OCR.")
    image = Image.open(io.BytesIO(pixmap.tobytes("png")))
    image.load()
    return image


def run_document_ocr(
    document_id: str,
    *,
    engine: OCREngine | None = None,
    language: str | None = None,
    dpi: int = OCR_DPI,
    page_numbers: set[int] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    timeout_seconds: int = 60,
) -> OCRRunResult:
    """OCR pending/failed pages; commit each page so interruption preserves progress."""
    try:
        document = get_document(document_id)
    except DocumentError as error:
        raise OCRError("The selected document could not be loaded from the local library.") from error
    if document is None:
        raise OCRError("That document is no longer in the library.")
    try:
        path = resolve_stored_document_path(document["stored_filename"])
        file_bytes = path.read_bytes()
        _, assessments = assess_pdf_pages(file_bytes, minimum_native_chars=OCR_MIN_NATIVE_CHARS)
    except (OSError, PDFProcessingError, ValueError) as error:
        raise OCRError("The stored PDF could not be read safely. Check the document library and retry.") from error
    assessment_map = {item.page_number: item for item in assessments}
    lang = (language or OCR_LANGUAGE).strip()
    if not lang or any(not part.replace("_", "").isalnum() for part in lang.split("+")):
        raise OCRError("OCR language must use Tesseract language codes such as 'eng' or 'eng+fra'.")
    if not 72 <= int(dpi) <= 300:
        raise OCRError("OCR resolution must be between 72 and 300 DPI.")
    try:
        with get_connection() as connection:
            for assessment in assessments:
                connection.execute(
                    """INSERT INTO document_ocr_pages(document_id,page_number,status) VALUES(?,?,?)
                       ON CONFLICT(document_id,page_number) DO NOTHING""",
                    (document_id, assessment.page_number, assessment.status),
                )
            rows = connection.execute("SELECT page_number,status,language,dpi FROM document_ocr_pages WHERE document_id=?", (document_id,)).fetchall()
    except sqlite3.Error as error:
        raise OCRError("OCR page status could not be loaded from the local database. Check storage and retry.") from error
    statuses = {int(row["page_number"]): dict(row) for row in rows}
    eligible = []
    for number, assessment in assessment_map.items():
        current = statuses.get(number, {})
        if assessment.status == "native_text":
            continue
        if page_numbers is not None and number not in page_numbers:
            continue
        if assessment.status in {"pending", "failed"} and current.get("status") != "complete":
            eligible.append(number)
        elif current.get("status") == "empty":
            eligible.append(number)
        elif current.get("status") == "complete" and (current.get("language") != lang or current.get("dpi") != int(dpi)):
            eligible.append(number)
    if not eligible:
        remaining = sum(1 for row in statuses.values() if row["status"] in {"pending", "failed", "empty"})
        return OCRRunResult(document_id, 0, 0, 0, 0, remaining)
    selected_engine = engine or TesseractEngine()
    available = selected_engine.languages()
    missing = set(lang.split("+")) - available
    if missing:
        raise OCRError(f"Tesseract language data is missing for: {', '.join(sorted(missing))}. Install those language packs and retry.")
    try:
        pdf = fitz.open(stream=file_bytes, filetype="pdf")
    except (fitz.FileDataError, RuntimeError, ValueError) as error:
        raise OCRError("The PDF could not be opened for OCR. It may be damaged or encrypted.") from error
    processed = succeeded = empty = failed = 0
    errors = []
    try:
        version = selected_engine.version()
        for index, number in enumerate(eligible, start=1):
            try:
                image = _render_page(pdf[number - 1], int(dpi))
                try:
                    recognized = selected_engine.recognize(image, lang, timeout_seconds).strip()
                finally:
                    image.close()
                status = "complete" if len(recognized) >= 2 else "empty"
                with get_connection() as connection:
                    if status == "complete":
                        connection.execute(
                            """INSERT INTO document_ocr_pages(document_id,page_number,status,recognized_text,language,dpi,engine_version,recognized_at,last_error)
                               VALUES(?,?,?,?,?,?,?,?,NULL) ON CONFLICT(document_id,page_number) DO UPDATE SET
                               status='complete',recognized_text=excluded.recognized_text,language=excluded.language,dpi=excluded.dpi,
                               engine_version=excluded.engine_version,recognized_at=excluded.recognized_at,last_error=NULL""",
                            (document_id, number, status, recognized, lang, int(dpi), version, datetime.now().astimezone().isoformat(timespec="seconds")),
                        )
                        connection.execute(
                            "UPDATE document_index_status SET status=CASE WHEN status='indexed' THEN 'out_of_date' WHEN status='no_text' THEN 'not_indexed' ELSE status END, error_message=NULL WHERE document_id=? AND status IN ('indexed','no_text')",
                            (document_id,),
                        )
                        succeeded += 1
                    else:
                        connection.execute(
                            """INSERT INTO document_ocr_pages(document_id,page_number,status,language,dpi,last_error)
                               VALUES(?,?,'empty',?,?,'Tesseract returned no usable text.') ON CONFLICT(document_id,page_number) DO UPDATE SET
                               status=CASE WHEN document_ocr_pages.status='complete' THEN 'complete' ELSE 'empty' END,
                               language=CASE WHEN document_ocr_pages.status='complete' THEN document_ocr_pages.language ELSE excluded.language END,
                               dpi=CASE WHEN document_ocr_pages.status='complete' THEN document_ocr_pages.dpi ELSE excluded.dpi END,
                               last_error=excluded.last_error""",
                            (document_id, number, lang, int(dpi)),
                        )
                        empty += 1
            except (OCRError, RuntimeError, OSError, ValueError, MemoryError) as error:
                message = str(error) if isinstance(error, OCRError) else "OCR failed on this page. You can retry it."
                with get_connection() as connection:
                    connection.execute(
                        """INSERT INTO document_ocr_pages(document_id,page_number,status,language,dpi,last_error)
                           VALUES(?,?,'failed',?,?,?) ON CONFLICT(document_id,page_number) DO UPDATE SET
                           status=CASE WHEN document_ocr_pages.status='complete' THEN 'complete' ELSE 'failed' END,
                           language=CASE WHEN document_ocr_pages.status='complete' THEN document_ocr_pages.language ELSE excluded.language END,
                           dpi=CASE WHEN document_ocr_pages.status='complete' THEN document_ocr_pages.dpi ELSE excluded.dpi END,
                           last_error=excluded.last_error""",
                        (document_id, number, lang, int(dpi), message[:300]),
                    )
                errors.append(f"Page {number}: {message}")
                failed += 1
            processed += 1
            if progress_callback:
                progress_callback(processed, len(eligible))
    except sqlite3.Error as error:
        raise OCRError("OCR text could not be saved to the local database. Previously completed pages remain available; retry later.") from error
    finally:
        pdf.close()
    try:
        with get_connection() as connection:
            remaining = connection.execute(
                "SELECT COUNT(*) FROM document_ocr_pages WHERE document_id=? AND status IN ('pending','failed','empty')", (document_id,)
            ).fetchone()[0]
    except sqlite3.Error as error:
        raise OCRError("OCR completed, but its final status could not be read from the local database.") from error
    return OCRRunResult(document_id, processed, succeeded, empty, failed, int(remaining), tuple(errors))
