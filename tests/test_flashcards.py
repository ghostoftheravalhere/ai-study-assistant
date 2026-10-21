"""Phase 7 tests for local flashcards, spaced repetition, and generated proposals."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from utils import database
from utils.flashcard_repository import (
    FlashcardRepositoryError,
    archive_card,
    archive_deck,
    create_card,
    create_cards_batch,
    create_deck,
    get_card,
    get_due_cards,
    get_flashcard_stats,
    list_cards,
    list_decks,
    list_review_history,
    record_card_review,
    update_card,
    update_deck,
)
from utils.flashcard_scheduler import MAX_INTERVAL_DAYS, schedule_review
from utils.flashcard_service import FlashcardValidationError, generate_document_flashcards, parse_generated_flashcards
from utils.gemini_service import GeminiService


@pytest.fixture
def flashcard_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "phase7.sqlite3")
    database.initialize_database()
    document_id = "phase7-source"
    with database.get_connection() as connection:
        connection.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)",
            (document_id, "biology.pdf", "biology-stored.pdf", 100, 4, "2026-01-01", "ready", "--- Page 1 --- cell membrane", "phase7-hash"),
        )
    return document_id, str(database.DATABASE_PATH)


def make_card(deck_id: str, front="What is a cell?", **kwargs) -> str:
    return create_card(
        deck_id, front=front, back=kwargs.pop("back", "The basic unit of life."),
        explanation=kwargs.pop("explanation", "Cells form living organisms."), **kwargs,
    )


def test_additive_migration_is_idempotent_and_preserves_existing_records(flashcard_db):
    _, db_path = flashcard_db
    with database.get_connection() as connection:
        connection.execute("INSERT INTO study_activity(activity_type,description,created_at) VALUES('legacy','preserve me','2026-01-01')")
        connection.execute("INSERT INTO quizzes(id,title,source_filename,difficulty,requested_question_count,created_at) VALUES('legacy-quiz','Legacy','old.pdf','Easy',5,'2026-01-01')")
    database.initialize_database()
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT description FROM study_activity WHERE activity_type='legacy'").fetchone()[0] == "preserve me"
        assert connection.execute("SELECT title FROM quizzes WHERE id='legacy-quiz'").fetchone()[0] == "Legacy"
        assert {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
            "flashcard_decks", "flashcards", "flashcard_schedule", "flashcard_reviews"
        }


def test_deck_create_search_rename_description_and_archive_restore(flashcard_db):
    deck = create_deck("Biology", description="Cell structures", category="Science", source_document_id=flashcard_db[0])
    make_card(deck)
    assert list_decks(query="cell")[0]["card_count"] == 1
    update_deck(deck, title="Cell biology", description="Updated", category="Biology")
    assert get_card(list_cards(deck_id=deck)[0]["id"])["deck_title"] == "Cell biology"
    archive_deck(deck)
    assert list_decks() == []
    assert get_card(list_cards(deck_id=deck, include_archived=True)[0]["id"]) is None
    archive_deck(deck, archived=False)
    assert list_decks()[0]["title"] == "Cell biology"
    with pytest.raises(FlashcardRepositoryError):
        create_deck("  ")
    with pytest.raises(FlashcardRepositoryError):
        update_deck("missing", title="No deck")


@pytest.mark.parametrize("values", [
    {"front": "", "back": "answer"},
    {"front": "question", "back": "  "},
    {"front": "x" * 501, "back": "answer"},
    {"front": "question", "back": "x" * 5001},
])
def test_manual_card_validation_rejects_empty_and_oversized_content(flashcard_db, values):
    deck = create_deck("Validation")
    with pytest.raises(FlashcardRepositoryError):
        create_card(deck, **values)


def test_card_edit_move_archive_restore_and_source_snapshot_survive_document_deletion(flashcard_db):
    doc_id, db_path = flashcard_db
    first, second = create_deck("A"), create_deck("B")
    card_id = make_card(first, source_document_id=doc_id, source_pages=[1, 3], topic="Biology")
    assert get_card(card_id)["source_filename"] == "biology.pdf"
    update_card(card_id, second, front="Updated front", back="Updated back", explanation="More detail", topic="Cells", source_document_id=doc_id, source_pages=[2])
    assert get_card(card_id)["deck_id"] == second
    assert get_card(card_id)["source_pages"] == [2]
    with pytest.raises(FlashcardRepositoryError):
        make_card(second, source_document_id=doc_id, source_pages=[5])
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    card = get_card(card_id)
    assert card["source_document_id"] is None and card["source_filename"] == "biology.pdf"
    archive_card(card_id)
    assert get_card(card_id) is None
    archive_card(card_id, archived=False)
    assert get_card(card_id)["front"] == "Updated front"


def test_batch_card_save_is_atomic_when_a_database_insert_fails(flashcard_db):
    deck = create_deck("Batch")
    with database.get_connection() as connection:
        connection.execute("CREATE TRIGGER fail_card BEFORE INSERT ON flashcards WHEN NEW.front='trigger failure' BEGIN SELECT RAISE(ABORT,'simulated'); END")
    with pytest.raises(sqlite3.IntegrityError):
        create_cards_batch(deck, [
            {"front": "first", "back": "one"},
            {"front": "trigger failure", "back": "two"},
        ])
    assert list_cards(deck_id=deck) == []


@pytest.mark.parametrize(("rating", "expected_days", "state", "success", "lapse"), [
    ("again", 10 / 1440, "learning", False, 1),
    ("hard", 1, "review", True, 0),
    ("good", 2, "review", True, 0),
    ("easy", 4, "review", True, 0),
])
def test_scheduler_new_card_ratings_are_deterministic(rating, expected_days, state, success, lapse):
    now = datetime(2026, 10, 21, 9, 30, tzinfo=timezone.utc)
    new = {"review_count": 0, "successful_recall_count": 0, "lapse_count": 0, "interval_days": 0, "ease_factor": 2.5, "learning_state": "new"}
    result = schedule_review(new, rating, now=now)
    assert result.interval_days == pytest.approx(expected_days)
    assert result.learning_state == state
    assert result.recall_successful is success
    assert result.lapse_count == lapse
    assert datetime.fromisoformat(result.reviewed_at) == now
    assert datetime.fromisoformat(result.due_at) > now
    assert schedule_review(new, rating, now=now) == result


def test_scheduler_mature_rating_growth_ease_and_max_interval():
    previous = {"review_count": 3, "successful_recall_count": 3, "lapse_count": 0, "interval_days": 5, "ease_factor": 2.5, "learning_state": "review"}
    now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    hard = schedule_review(previous, "hard", now=now)
    good = schedule_review(previous, "good", now=now)
    easy = schedule_review(previous, "easy", now=now)
    assert hard.interval_days < good.interval_days < easy.interval_days
    assert hard.ease_factor == 2.35 and easy.ease_factor == 2.65
    learning = {**previous, "review_count": 1, "interval_days": 10 / 1440, "learning_state": "learning"}
    assert schedule_review(learning, "hard", now=now).interval_days < schedule_review(learning, "good", now=now).interval_days < schedule_review(learning, "easy", now=now).interval_days
    high = {**previous, "interval_days": MAX_INTERVAL_DAYS}
    assert schedule_review(high, "easy", now=now).interval_days == MAX_INTERVAL_DAYS
    with pytest.raises(ValueError):
        schedule_review(previous, "bad", now=now)


def test_due_queue_order_filter_future_exclusion_and_new_daily_allowance(flashcard_db):
    first_deck, second_deck = create_deck("First"), create_deck("Second")
    a = make_card(first_deck, "Overdue")
    b = make_card(first_deck, "Due")
    c = make_card(second_deck, "Other deck")
    future = make_card(first_deck, "Future")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for card_id, session_id, date_value in (
        (a, "a", now - timedelta(days=3)),
        (b, "b", now - timedelta(days=1)),
        (c, "c", now - timedelta(days=2)),
        (future, "f", now - timedelta(days=4)),
    ):
        record_card_review(card_id, session_id, "good", answer_revealed=True, now=date_value)
    with database.get_connection() as connection:
        connection.execute("UPDATE flashcard_schedule SET due_at=? WHERE card_id=?", ((now - timedelta(days=2)).isoformat(), a))
        connection.execute("UPDATE flashcard_schedule SET due_at=? WHERE card_id=?", ((now - timedelta(seconds=1)).isoformat(), b))
        connection.execute("UPDATE flashcard_schedule SET due_at=? WHERE card_id=?", ((now + timedelta(days=2)).isoformat(), c))
        connection.execute("UPDATE flashcard_schedule SET due_at=? WHERE card_id=?", ((now + timedelta(days=1)).isoformat(), future))
    queue = get_due_cards(now=now, include_new=False)
    assert [item["id"] for item in queue] == [a, b]
    assert [item["id"] for item in get_due_cards(now=now, include_new=False, due_kind="overdue")] == [a]
    assert [item["id"] for item in get_due_cards(now=now, include_new=False, due_kind="due_today")] == [b]
    assert [item["id"] for item in get_due_cards(deck_id=first_deck, now=now, include_new=False)] == [a, b]
    # A first review consumes the daily new-card allowance globally across decks.
    new1, new2 = make_card(first_deck, "Fresh 1"), make_card(second_deck, "Fresh 2")
    new_queue = get_due_cards(deck_id=second_deck, now=now, include_new=True, daily_new_limit=1)
    assert len(new_queue) == 1 and new_queue[0]["id"] == new2
    record_card_review(new2, "new-limit-session", "again", answer_revealed=True, now=now)
    after_daily_limit = get_due_cards(deck_id=first_deck, now=now, include_new=True, daily_new_limit=1)
    assert [item["id"] for item in after_daily_limit] == [a, b]


def test_review_requires_reveal_is_idempotent_and_appends_history(flashcard_db):
    deck = create_deck("Recall")
    card = make_card(deck)
    with pytest.raises(FlashcardRepositoryError, match="Reveal"):
        record_card_review(card, "s1", "good", answer_revealed=False)
    event = record_card_review(card, "s1", "good", answer_revealed=True)
    duplicate = record_card_review(card, "s1", "good", answer_revealed=True)
    assert event["duplicate"] is False and duplicate["duplicate"] is True
    with pytest.raises(FlashcardRepositoryError, match="already been rated"):
        record_card_review(card, "s1", "easy", answer_revealed=True)
    current = get_card(card)
    assert current["review_count"] == current["successful_recall_count"] == 1
    assert len(list_review_history()) == 1
    assert current["interval_days"] == 2


def test_review_transaction_failure_does_not_update_schedule_or_add_partial_event(flashcard_db):
    deck = create_deck("Atomic")
    card = make_card(deck)
    before = get_card(card)
    with database.get_connection() as connection:
        connection.execute("CREATE TRIGGER fail_schedule BEFORE UPDATE ON flashcard_schedule BEGIN SELECT RAISE(ABORT,'simulated'); END")
    with pytest.raises(sqlite3.IntegrityError):
        record_card_review(card, "atomic-session", "good", answer_revealed=True)
    assert get_card(card)["review_count"] == before["review_count"] == 0
    assert list_review_history() == []


def test_archived_deck_excluded_but_card_and_review_history_remain(flashcard_db):
    deck = create_deck("Archive")
    card = make_card(deck)
    record_card_review(card, "history-session", "easy", answer_revealed=True)
    archive_deck(deck)
    assert get_due_cards(include_new=True) == []
    assert get_card(card) is None
    assert len(list_review_history()) == 1
    assert list_cards(include_archived=True)[0]["front"] == "What is a cell?"


def test_statistics_empty_and_rating_distribution_and_success_denominator(flashcard_db):
    assert get_flashcard_stats()["total_reviews"] == 0
    assert get_flashcard_stats()["recall_success_rate"] is None
    deck = create_deck("Stats")
    cards = [make_card(deck, f"Card {i}") for i in range(4)]
    for index, rating in enumerate(("again", "hard", "good", "easy")):
        record_card_review(cards[index], f"stats-{index}", rating, answer_revealed=True)
    stats = get_flashcard_stats()
    assert stats["total_cards"] == 4 and stats["deck_count"] == 1
    assert stats["total_reviews"] == stats["reviewed_today"] == 4
    assert stats["recall_success_rate"] == 75.0
    assert stats["rating_distribution"] == {"again": 1, "hard": 1, "good": 1, "easy": 1}


def test_due_today_and_overdue_use_the_running_hosts_local_calendar_day(flashcard_db):
    from utils.flashcard_repository import _local_day_bounds

    deck = create_deck("Calendar")
    today, overdue, tomorrow = (make_card(deck, label) for label in ("Today", "Overdue", "Tomorrow"))
    now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    start, end = _local_day_bounds(now)
    with database.get_connection() as connection:
        connection.executemany(
            "UPDATE flashcard_schedule SET due_at=? WHERE card_id=?",
            [
                ((datetime.fromisoformat(end) - timedelta(seconds=1)).isoformat(), today),
                ((datetime.fromisoformat(start) - timedelta(seconds=1)).isoformat(), overdue),
                (end, tomorrow),
            ],
        )
    stats = get_flashcard_stats(now=now)
    assert stats["due_today"] == 1
    assert stats["overdue_cards"] == 1


def raw_generated_card(i, **overrides):
    card = {
        "front": f"Define concept {i}.", "back": f"Concept {i} means a useful supported explanation.",
        "explanation": "The supplied passage explains the concept.", "topic": "Biology",
        "source_pages": [1, 4, 99, True],
    }
    card.update(overrides)
    return card


def test_generated_card_validation_deduplication_schema_and_page_filtering():
    data = {"cards": [raw_generated_card(1), raw_generated_card(1), *(raw_generated_card(i, source_pages=[2]) for i in range(2, 6))]}
    cards = parse_generated_flashcards(json.dumps(data), requested_count=5, source_document_id="source", allowed_source_pages={1, 2, 4})
    assert len(cards) == 5
    assert cards[0]["source_pages"] == [1, 4]
    assert cards[1]["source_pages"] == [2]
    assert cards[0]["source_document_id"] == "source"
    for broken in (raw_generated_card(1, front=""), raw_generated_card(1, back=" "), raw_generated_card(1, front="x" * 501)):
        with pytest.raises(FlashcardValidationError):
            parse_generated_flashcards(json.dumps({"cards": [broken, *(raw_generated_card(i) for i in range(2, 6))]}), requested_count=10, source_document_id=None, allowed_source_pages={1})
    with pytest.raises(FlashcardValidationError, match="unreadable"):
        parse_generated_flashcards("bad json", requested_count=5, source_document_id=None, allowed_source_pages=set())


def test_generate_document_flashcards_uses_injected_gemini_service_without_embedding_load(monkeypatch):
    from utils import flashcard_service

    monkeypatch.setattr(flashcard_service, "list_active_chunks", lambda *_args: [])
    class FakeGemini:
        def generate_flashcards(self, text, **kwargs):
            assert "Page 2" in text
            assert kwargs["allowed_source_pages"] == {2}
            return type("Result", (), {"cards": [{"front": "Question", "back": "Answer"}], "context_truncated": False, "original_context_length": len(text)})()
    result = generate_document_flashcards(
        {"id": "doc", "extracted_text": "--- Page 2 --- facts"}, count=5,
        complexity="Basic", style="Definitions", gemini_service=FakeGemini(),
    )
    assert result.cards[0]["front"] == "Question"
    assert result.context_source == "bounded extracted PDF text"


def test_gemini_flashcard_generation_uses_central_service_schema_and_key_injection():
    payload = json.dumps({"cards": [raw_generated_card(i) for i in range(1, 6)]})
    calls = []
    api_keys = []

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"text": payload})()

    class Client:
        models = Models()

        def close(self):
            pass

    def client_factory(*, api_key):
        api_keys.append(api_key)
        return Client()

    result = GeminiService(api_key="isolated-test-key", client_factory=client_factory).generate_flashcards(
        "--- Page 1 --- source facts", count=5, complexity="Intermediate", style="Processes",
        topic_focus="Cells", source_document_id="doc", allowed_source_pages={1},
    )
    assert len(result.cards) == 5
    assert result.cards[0]["source_pages"] == [1]
    assert api_keys == ["isolated-test-key"]
    assert calls[0]["config"].response_mime_type == "application/json"
    assert calls[0]["config"].response_json_schema["required"] == ["cards"]


def test_flashcard_generation_activity_is_generic_and_does_not_store_document_or_prompt(flashcard_db):
    database.record_ai_activity("flashcards_generated", "Generated flashcard proposals")
    with database.get_connection() as connection:
        row = connection.execute("SELECT activity_type,description FROM study_activity ORDER BY id DESC LIMIT 1").fetchone()
    assert tuple(row) == ("flashcards_generated", "Generated flashcard proposals")


@pytest.mark.parametrize("page", ["Flashcards", "Study Progress"])
def test_flashcard_pages_render_on_empty_local_db_without_model_or_key(flashcard_db, monkeypatch, page):
    from utils import gemini_service

    for name in ("GEMINI_API_KEY", *(f"GEMINI_API_KEY_{index}" for index in range(1, 5))):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(gemini_service, "_read_secret_source", lambda: {})
    app = AppTest.from_file(Path(__file__).resolve().parent.parent / "app.py", default_timeout=10)
    app.session_state["active_page"] = page
    app.run()
    assert not app.exception
