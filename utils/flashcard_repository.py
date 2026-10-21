"""SQLite persistence for decks, flashcards, scheduling, and append-only reviews."""

import json
import sqlite3
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping, Sequence

from utils.database import get_connection
from utils.flashcard_scheduler import RATINGS, schedule_review


MAX_FRONT_CHARS = 500
MAX_BACK_CHARS = 5_000
MAX_EXPLANATION_CHARS = 3_000
MAX_DESCRIPTION_CHARS = 1_000


class FlashcardRepositoryError(ValueError):
    """Safe error for invalid flashcard operations or local storage failures."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _clean_text(value: Any, *, label: str, required: bool, max_chars: int) -> str:
    if not isinstance(value, str):
        raise FlashcardRepositoryError(f"{label} must be text.")
    clean = value.strip()
    if required and not clean:
        raise FlashcardRepositoryError(f"{label} cannot be empty.")
    if len(clean) > max_chars:
        raise FlashcardRepositoryError(f"{label} must be {max_chars:,} characters or fewer.")
    return clean


def _source_metadata(
    connection,
    document_id: str | None,
    source_filename: str | None,
    source_pages: Sequence[int] | None,
) -> tuple[str | None, str | None, list[int]]:
    pages: list[int] = []
    for page in source_pages or []:
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise FlashcardRepositoryError("Source page references must be positive page numbers.")
        if page not in pages:
            pages.append(page)
    filename = source_filename.strip() if isinstance(source_filename, str) and source_filename.strip() else None
    if document_id:
        document = connection.execute(
            "SELECT original_filename, page_count FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if document is None:
            raise FlashcardRepositoryError("The selected source PDF is no longer available.")
        filename = document["original_filename"]
        if any(page > document["page_count"] for page in pages):
            raise FlashcardRepositoryError("A source page is outside the selected PDF.")
    return document_id, filename, pages


def create_deck(
    title: str,
    *,
    description: str = "",
    category: str | None = None,
    source_document_id: str | None = None,
    source_filename: str | None = None,
) -> str:
    clean_title = _clean_text(title, label="Deck title", required=True, max_chars=120)
    clean_description = _clean_text(description, label="Deck description", required=False, max_chars=MAX_DESCRIPTION_CHARS)
    clean_category = _clean_text(category or "", label="Category", required=False, max_chars=100) or None
    deck_id, now = _new_id(), _iso()
    with get_connection() as connection:
        source_document_id, filename, _ = _source_metadata(connection, source_document_id, source_filename, None)
        connection.execute(
            """INSERT INTO flashcard_decks
               (id,title,description,category,source_document_id,source_filename,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (deck_id, clean_title, clean_description, clean_category, source_document_id, filename, now, now),
        )
    return deck_id


def list_decks(*, query: str = "", include_archived: bool = False) -> list[dict[str, Any]]:
    sql = """SELECT d.*, (SELECT COUNT(*) FROM flashcards c WHERE c.deck_id=d.id AND c.archived_at IS NULL) AS card_count,
                     (SELECT COUNT(*) FROM flashcards c JOIN flashcard_schedule s ON s.card_id=c.id
                       WHERE c.deck_id=d.id AND c.archived_at IS NULL AND s.due_at<=?) AS due_count,
                     (SELECT COUNT(*) FROM flashcard_reviews r JOIN flashcards c ON c.id=r.card_id
                       WHERE c.deck_id=d.id) AS review_count
              FROM flashcard_decks d WHERE 1=1"""
    params: list[Any] = [_iso()]
    if not include_archived:
        sql += " AND d.archived_at IS NULL"
    if query.strip():
        sql += " AND (d.title LIKE ? OR d.category LIKE ? OR d.description LIKE ?)"
        match = f"%{query.strip()}%"
        params.extend((match, match, match))
    sql += " ORDER BY d.archived_at IS NOT NULL, d.title COLLATE NOCASE, d.created_at"
    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_deck(deck_id: str, *, include_archived: bool = True) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM flashcard_decks WHERE id=?", (deck_id,)).fetchone()
    if row is None or (row["archived_at"] is not None and not include_archived):
        return None
    return dict(row)


def update_deck(deck_id: str, *, title: str, description: str = "", category: str | None = None) -> None:
    clean_title = _clean_text(title, label="Deck title", required=True, max_chars=120)
    clean_description = _clean_text(description, label="Deck description", required=False, max_chars=MAX_DESCRIPTION_CHARS)
    clean_category = _clean_text(category or "", label="Category", required=False, max_chars=100) or None
    with get_connection() as connection:
        result = connection.execute(
            """UPDATE flashcard_decks SET title=?,description=?,category=?,updated_at=?
               WHERE id=? AND archived_at IS NULL""",
            (clean_title, clean_description, clean_category, _iso(), deck_id),
        )
        if result.rowcount != 1:
            raise FlashcardRepositoryError("That active deck could not be found.")


def archive_deck(deck_id: str, *, archived: bool = True) -> None:
    with get_connection() as connection:
        result = connection.execute(
            "UPDATE flashcard_decks SET archived_at=?,updated_at=? WHERE id=?",
            (_iso() if archived else None, _iso(), deck_id),
        )
        if result.rowcount != 1:
            raise FlashcardRepositoryError("That deck could not be found.")


def _validate_card_payload(card: Mapping[str, Any]) -> dict[str, Any]:
    front = _clean_text(card.get("front"), label="Card front", required=True, max_chars=MAX_FRONT_CHARS)
    back = _clean_text(card.get("back"), label="Card back", required=True, max_chars=MAX_BACK_CHARS)
    explanation = _clean_text(card.get("explanation", ""), label="Explanation", required=False, max_chars=MAX_EXPLANATION_CHARS)
    topic = _clean_text(card.get("topic") or "", label="Topic", required=False, max_chars=120) or None
    difficulty = _clean_text(card.get("difficulty") or "", label="Difficulty", required=False, max_chars=30) or None
    return {"front": front, "back": back, "explanation": explanation, "topic": topic,
            "difficulty": difficulty, "source_document_id": card.get("source_document_id"),
            "source_filename": card.get("source_filename"), "source_pages": card.get("source_pages", [])}


def _insert_card(connection, deck_id: str, card_id: str, card: Mapping[str, Any], now: str) -> None:
    deck = connection.execute("SELECT id FROM flashcard_decks WHERE id=? AND archived_at IS NULL", (deck_id,)).fetchone()
    if deck is None:
        raise FlashcardRepositoryError("Choose an active flashcard deck.")
    document_id, filename, pages = _source_metadata(
        connection, card.get("source_document_id"), card.get("source_filename"), card.get("source_pages", [])
    )
    connection.execute(
        """INSERT INTO flashcards
           (id,deck_id,front,back,explanation,topic,difficulty,source_document_id,source_filename,
            source_pages_json,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (card_id, deck_id, card["front"], card["back"], card["explanation"], card["topic"], card["difficulty"],
         document_id, filename, json.dumps(pages), now, now),
    )
    connection.execute(
        "INSERT INTO flashcard_schedule (card_id,due_at) VALUES (?,?)", (card_id, now)
    )
    connection.execute("UPDATE flashcard_decks SET updated_at=? WHERE id=?", (now, deck_id))


def create_card(deck_id: str, **card: Any) -> str:
    clean = _validate_card_payload(card)
    card_id, now = _new_id(), _iso()
    with get_connection() as connection:
        _insert_card(connection, deck_id, card_id, clean, now)
    return card_id


def create_cards_batch(deck_id: str, cards: Sequence[Mapping[str, Any]]) -> list[str]:
    if not cards:
        raise FlashcardRepositoryError("Select at least one card to save.")
    validated = [_validate_card_payload(card) for card in cards]
    ids, now = [_new_id() for _ in validated], _iso()
    with get_connection() as connection:
        for card_id, card in zip(ids, validated):
            _insert_card(connection, deck_id, card_id, card, now)
    return ids


def get_card(card_id: str, *, include_archived: bool = False) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """SELECT c.*,s.review_count,s.successful_recall_count,s.lapse_count,s.interval_days,
                      s.ease_factor,s.due_at,s.last_reviewed_at,s.learning_state,d.title AS deck_title,
                      d.archived_at AS deck_archived_at
               FROM flashcards c JOIN flashcard_schedule s ON s.card_id=c.id
               JOIN flashcard_decks d ON d.id=c.deck_id WHERE c.id=?""",
            (card_id,),
        ).fetchone()
    if row is None or (not include_archived and (row["archived_at"] is not None or row["deck_archived_at"] is not None)):
        return None
    result = dict(row)
    try:
        result["source_pages"] = json.loads(result.pop("source_pages_json"))
    except (ValueError, TypeError):
        result["source_pages"] = []
    return result


def list_cards(
    *, deck_id: str | None = None, query: str = "", include_archived: bool = False, limit: int = 500
) -> list[dict[str, Any]]:
    sql = """SELECT c.id,c.deck_id,c.front,c.back,c.explanation,c.topic,c.difficulty,c.source_document_id,
                     c.source_filename,c.source_pages_json,c.created_at,c.updated_at,c.archived_at,d.title AS deck_title,
                     s.review_count,s.interval_days,s.due_at,s.learning_state
              FROM flashcards c JOIN flashcard_decks d ON d.id=c.deck_id
              JOIN flashcard_schedule s ON s.card_id=c.id WHERE 1=1"""
    params: list[Any] = []
    if not include_archived:
        sql += " AND c.archived_at IS NULL AND d.archived_at IS NULL"
    if deck_id:
        sql += " AND c.deck_id=?"
        params.append(deck_id)
    if query.strip():
        sql += " AND (c.front LIKE ? OR c.back LIKE ? OR c.topic LIKE ?)"
        match = f"%{query.strip()}%"
        params.extend((match, match, match))
    sql += " ORDER BY d.title COLLATE NOCASE,c.created_at,c.id LIMIT ?"
    params.append(max(1, min(int(limit), 2000)))
    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["source_pages"] = json.loads(item.pop("source_pages_json"))
        except (ValueError, TypeError):
            item["source_pages"] = []
        results.append(item)
    return results


def update_card(card_id: str, deck_id: str, **card: Any) -> None:
    clean = _validate_card_payload(card)
    now = _iso()
    with get_connection() as connection:
        if connection.execute("SELECT 1 FROM flashcards WHERE id=? AND archived_at IS NULL", (card_id,)).fetchone() is None:
            raise FlashcardRepositoryError("That active flashcard could not be found.")
        if connection.execute("SELECT 1 FROM flashcard_decks WHERE id=? AND archived_at IS NULL", (deck_id,)).fetchone() is None:
            raise FlashcardRepositoryError("Choose an active flashcard deck.")
        document_id, filename, pages = _source_metadata(
            connection, clean["source_document_id"], clean["source_filename"], clean["source_pages"]
        )
        connection.execute(
            """UPDATE flashcards SET deck_id=?,front=?,back=?,explanation=?,topic=?,difficulty=?,
               source_document_id=?,source_filename=?,source_pages_json=?,updated_at=? WHERE id=?""",
            (deck_id, clean["front"], clean["back"], clean["explanation"], clean["topic"], clean["difficulty"],
             document_id, filename, json.dumps(pages), now, card_id),
        )
        connection.execute("UPDATE flashcard_decks SET updated_at=? WHERE id=?", (now, deck_id))


def archive_card(card_id: str, *, archived: bool = True) -> None:
    with get_connection() as connection:
        result = connection.execute(
            "UPDATE flashcards SET archived_at=?,updated_at=? WHERE id=?",
            (_iso() if archived else None, _iso(), card_id),
        )
        if result.rowcount != 1:
            raise FlashcardRepositoryError("That card could not be found.")


def _local_day_bounds(now: datetime | None = None) -> tuple[str, str]:
    local = (now or datetime.now().astimezone()).astimezone()
    # Convert each naive calendar midnight separately so the OS local zone can
    # apply the correct UTC offset on both sides of a daylight-saving change.
    start_local = datetime.combine(local.date(), time.min).astimezone()
    end_local = datetime.combine(local.date() + timedelta(days=1), time.min).astimezone()
    return _iso(start_local), _iso(end_local)


def get_flashcard_stats(*, now: datetime | None = None) -> dict[str, Any]:
    instant = _iso(now or _now())
    start_day, end_day = _local_day_bounds(now)
    with get_connection() as connection:
        cards = connection.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN s.review_count=0 THEN 1 ELSE 0 END) AS new_count,
                      SUM(CASE WHEN s.due_at>=? AND s.due_at<? THEN 1 ELSE 0 END) AS due_count,
                      SUM(CASE WHEN s.due_at<? THEN 1 ELSE 0 END) AS overdue_count
               FROM flashcards c JOIN flashcard_schedule s ON s.card_id=c.id
               JOIN flashcard_decks d ON d.id=c.deck_id
               WHERE c.archived_at IS NULL AND d.archived_at IS NULL""",
            (start_day, end_day, start_day),
        ).fetchone()
        decks = connection.execute("SELECT COUNT(*) FROM flashcard_decks WHERE archived_at IS NULL").fetchone()[0]
        reviews_today = connection.execute(
            "SELECT COUNT(*) FROM flashcard_reviews WHERE reviewed_at>=? AND reviewed_at<?", (start_day, end_day)
        ).fetchone()[0]
        review_count = connection.execute("SELECT COUNT(*) FROM flashcard_reviews").fetchone()[0]
        successful_count = connection.execute("SELECT COUNT(*) FROM flashcard_reviews WHERE recall_successful=1").fetchone()[0]
        distribution = {rating: 0 for rating in RATINGS}
        for row in connection.execute("SELECT rating,COUNT(*) AS count FROM flashcard_reviews GROUP BY rating"):
            distribution[row["rating"]] = row["count"]
    return {
        "total_cards": cards["total"] or 0,
        "new_cards": cards["new_count"] or 0,
        "due_today": cards["due_count"] or 0,
        "overdue_cards": cards["overdue_count"] or 0,
        "deck_count": decks,
        "reviewed_today": reviews_today,
        "total_reviews": review_count,
        "rating_distribution": distribution,
        "recall_success_rate": round(successful_count * 100 / review_count, 1) if review_count else None,
    }


def _new_cards_reviewed_today(connection, start_day: str, end_day: str) -> int:
    sql = """SELECT COUNT(*) FROM flashcard_reviews r JOIN flashcards c ON c.id=r.card_id
             WHERE r.was_new=1 AND r.reviewed_at>=? AND r.reviewed_at<? AND c.archived_at IS NULL"""
    return connection.execute(sql, (start_day, end_day)).fetchone()[0]


def get_due_cards(
    *, deck_id: str | None = None, include_new: bool = False, due_kind: str = "all",
    daily_new_limit: int = 10,
    session_limit: int = 30, now: datetime | None = None,
) -> list[dict[str, Any]]:
    if not 0 <= daily_new_limit <= 100:
        raise FlashcardRepositoryError("The daily new-card limit must be between 0 and 100.")
    if due_kind not in {"all", "overdue", "due_today"}:
        raise FlashcardRepositoryError("Choose all due cards, overdue cards, or cards due today.")
    if not 1 <= session_limit <= 200:
        raise FlashcardRepositoryError("A review session must include between 1 and 200 cards.")
    instant = _iso(now or _now())
    start_day, end_day = _local_day_bounds(now)
    with get_connection() as connection:
        where = "c.archived_at IS NULL AND d.archived_at IS NULL AND s.review_count>0"
        if due_kind == "overdue":
            where += " AND s.due_at<?"
            params: list[Any] = [start_day]
        elif due_kind == "due_today":
            where += " AND s.due_at>=? AND s.due_at<=?"
            params = [start_day, instant]
        else:
            where += " AND s.due_at<=?"
            params = [instant]
        if deck_id:
            where += " AND c.deck_id=?"
            params.append(deck_id)
        due = connection.execute(
            f"""SELECT c.id,c.deck_id,c.front,c.back,c.explanation,c.topic,c.difficulty,c.source_document_id,
                       c.source_filename,c.source_pages_json,c.created_at,d.title AS deck_title,
                       s.review_count,s.interval_days,s.ease_factor,s.due_at,s.last_reviewed_at,s.learning_state
                FROM flashcards c JOIN flashcard_schedule s ON s.card_id=c.id
                JOIN flashcard_decks d ON d.id=c.deck_id WHERE {where}
                ORDER BY s.due_at,c.created_at,c.id LIMIT ?""",
            [*params, session_limit],
        ).fetchall()
        cards = [dict(row) for row in due]
        if include_new and len(cards) < session_limit:
            remaining_new = max(0, daily_new_limit - _new_cards_reviewed_today(connection, start_day, end_day))
            if remaining_new:
                new_where = "c.archived_at IS NULL AND d.archived_at IS NULL AND s.review_count=0"
                new_params: list[Any] = []
                if deck_id:
                    new_where += " AND c.deck_id=?"
                    new_params.append(deck_id)
                fresh = connection.execute(
                    f"""SELECT c.id,c.deck_id,c.front,c.back,c.explanation,c.topic,c.difficulty,c.source_document_id,
                               c.source_filename,c.source_pages_json,c.created_at,d.title AS deck_title,
                               s.review_count,s.interval_days,s.ease_factor,s.due_at,s.last_reviewed_at,s.learning_state
                        FROM flashcards c JOIN flashcard_schedule s ON s.card_id=c.id
                        JOIN flashcard_decks d ON d.id=c.deck_id WHERE {new_where}
                        ORDER BY c.created_at,c.id LIMIT ?""",
                    [*new_params, min(remaining_new, session_limit - len(cards))],
                ).fetchall()
                cards.extend(dict(row) for row in fresh)
    for card in cards:
        try:
            card["source_pages"] = json.loads(card.pop("source_pages_json"))
        except (ValueError, TypeError):
            card["source_pages"] = []
    return cards


def record_card_review(
    card_id: str,
    session_id: str,
    rating: str,
    *,
    answer_revealed: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_rating = rating.casefold() if isinstance(rating, str) else ""
    if normalized_rating not in RATINGS:
        raise FlashcardRepositoryError("Choose Again, Hard, Good, or Easy.")
    if not answer_revealed:
        raise FlashcardRepositoryError("Reveal the answer before rating your recall.")
    if not session_id or len(session_id) > 100:
        raise FlashcardRepositoryError("The study session is invalid. Start a new review session.")
    review_id = _new_id()
    with get_connection() as connection:
        previous = connection.execute(
            "SELECT * FROM flashcard_reviews WHERE session_id=? AND card_id=?", (session_id, card_id)
        ).fetchone()
        if previous:
            if previous["rating"] != normalized_rating:
                raise FlashcardRepositoryError("This card has already been rated in the current session.")
            return {**dict(previous), "duplicate": True}
        rows = connection.execute(
            """SELECT c.archived_at AS card_archived,d.archived_at AS deck_archived,
                      s.review_count,s.successful_recall_count,s.lapse_count,s.interval_days,s.ease_factor,
                      s.due_at,s.last_reviewed_at,s.learning_state
               FROM flashcards c JOIN flashcard_decks d ON d.id=c.deck_id
               JOIN flashcard_schedule s ON s.card_id=c.id WHERE c.id=?""",
            (card_id,),
        ).fetchone()
        if rows is None or rows["card_archived"] or rows["deck_archived"]:
            raise FlashcardRepositoryError("That card is archived or no longer available for review.")
        reviewed_at = now or _now()
        result = schedule_review(dict(rows), normalized_rating, now=reviewed_at)
        connection.execute(
            """INSERT INTO flashcard_reviews
               (id,card_id,session_id,rating,reviewed_at,previous_due_at,new_due_at,
                previous_interval_days,new_interval_days,was_new,recall_successful)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (review_id,card_id,session_id,normalized_rating,result.reviewed_at,rows["due_at"],result.due_at,
             result.previous_interval_days,result.interval_days,int(rows["review_count"] == 0),int(result.recall_successful)),
        )
        connection.execute(
            """UPDATE flashcard_schedule SET review_count=?,successful_recall_count=?,lapse_count=?,
               interval_days=?,ease_factor=?,due_at=?,last_reviewed_at=?,learning_state=? WHERE card_id=?""",
            (result.review_count,result.successful_recall_count,result.lapse_count,result.interval_days,
             result.ease_factor,result.due_at,result.reviewed_at,result.learning_state,card_id),
        )
        connection.execute("UPDATE flashcard_decks SET updated_at=? WHERE id=(SELECT deck_id FROM flashcards WHERE id=?)", (result.reviewed_at,card_id))
    return {"id": review_id, "card_id": card_id, "session_id": session_id, "rating": normalized_rating,
            "reviewed_at": result.reviewed_at, "previous_due_at": rows["due_at"], "new_due_at": result.due_at,
            "previous_interval_days": result.previous_interval_days, "new_interval_days": result.interval_days,
            "was_new": rows["review_count"] == 0, "recall_successful": result.recall_successful,
            "duplicate": False}


def list_review_history(*, limit: int = 100, deck_id: str | None = None) -> list[dict[str, Any]]:
    sql = """SELECT r.*,c.front,c.back,c.topic,c.deck_id,d.title AS deck_title
             FROM flashcard_reviews r JOIN flashcards c ON c.id=r.card_id
             JOIN flashcard_decks d ON d.id=c.deck_id WHERE 1=1"""
    params: list[Any] = []
    if deck_id:
        sql += " AND c.deck_id=?"
        params.append(deck_id)
    sql += " ORDER BY r.reviewed_at DESC,r.id DESC LIMIT ?"
    params.append(max(1,min(int(limit),500)))
    with get_connection() as connection:
        rows = connection.execute(sql,params).fetchall()
    return [dict(row) for row in rows]
