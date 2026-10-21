"""Local flashcard decks, generated proposals, active recall, and review history."""

import uuid
import sqlite3
from datetime import datetime

import streamlit as st

from components.document_picker import render_document_picker
from utils.documents import DocumentError, list_documents
from utils.database import record_ai_activity
from utils.flashcard_repository import (
    FlashcardRepositoryError,
    archive_card,
    archive_deck,
    create_card,
    create_cards_batch,
    create_deck,
    get_card,
    get_deck,
    get_due_cards,
    get_flashcard_stats,
    list_cards,
    list_decks,
    list_review_history,
    record_card_review,
    update_card,
    update_deck,
)
from utils.flashcard_scheduler import RATINGS, RATING_LABELS
from utils.flashcard_service import (
    FlashcardValidationError,
    generate_document_flashcards,
)
from utils.gemini_service import (
    GeminiServiceError,
    get_gemini_service_for_session,
    is_gemini_available_for_session,
    load_gemini_model,
)


def _safe_load(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (FlashcardRepositoryError, DocumentError) as error:
        st.error(str(error))
        return None


def _render_summary() -> None:
    stats = get_flashcard_stats()
    st.markdown('<div class="eyebrow">BUILD A DAILY STUDY HABIT</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="page-title">Flashcards</h1>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Use active recall to practise ideas from your notes and PDFs.</p>', unsafe_allow_html=True)
    a, b, c, d, e = st.columns(5)
    a.metric("Due today", stats["due_today"])
    b.metric("Overdue", stats["overdue_cards"])
    c.metric("Flashcards", stats["total_cards"])
    d.metric("Decks", stats["deck_count"])
    e.metric("Reviewed today", stats["reviewed_today"])
    rate = stats["recall_success_rate"]
    st.caption(
        f"{stats['total_reviews']} total reviews · Self-rated recall success: "
        f"{rate:.1f}%" if rate is not None else f"{stats['total_reviews']} total reviews · No recall ratings yet."
    )


def _active_decks():
    return list_decks()


def _render_decks() -> None:
    st.subheader("Decks")
    with st.expander("Create a deck", expanded=not _active_decks()):
        with st.form("flashcard_create_deck"):
            title = st.text_input("Deck title", max_chars=120)
            description = st.text_area("Description (optional)", max_chars=1000)
            category = st.text_input("Subject or category (optional)", max_chars=100)
            try:
                documents = list_documents()
            except DocumentError as error:
                documents = []
                st.error(str(error))
            ids = [None, *(item["id"] for item in documents)]
            doc_by_id = {item["id"]: item for item in documents}
            if st.session_state.get("flashcard_deck_source") not in ids:
                st.session_state.pop("flashcard_deck_source", None)
            source_id = st.selectbox("Source PDF (optional)", ids, format_func=lambda value: "No source PDF" if value is None else doc_by_id[value]["original_filename"], key="flashcard_deck_source")
            submitted = st.form_submit_button("Create deck", type="primary")
        if submitted:
            try:
                deck_id = create_deck(
                    title, description=description, category=category,
                    source_document_id=source_id,
                    source_filename=doc_by_id[source_id]["original_filename"] if source_id else None,
                )
                st.success("Deck created.")
                st.session_state["flashcard_selected_deck"] = deck_id
                st.rerun()
            except FlashcardRepositoryError as error:
                st.error(str(error))

    search = st.text_input("Search decks", key="flashcard_deck_search")
    show_archived = st.checkbox("Show archived decks", key="flashcard_show_archived_decks")
    decks = _safe_load(list_decks, query=search, include_archived=show_archived) or []
    for deck in decks:
        archived = bool(deck["archived_at"])
        label = f"{deck['title']} · {deck['card_count']} cards · {deck['due_count']} due" + (" · Archived" if archived else "")
        with st.expander(label):
            st.caption(f"{deck['review_count']} review event(s) · {deck['category'] or 'No category'}")
            if archived:
                st.caption("Archived decks and their cards remain saved, but are excluded from reviews.")
                if st.button("Restore deck", key=f"flashcard_restore_deck_{deck['id']}"):
                    try:
                        archive_deck(deck["id"], archived=False)
                        st.rerun()
                    except FlashcardRepositoryError as error:
                        st.error(str(error))
                continue
            with st.form(f"flashcard_edit_deck_{deck['id']}"):
                title_value = st.text_input("Title", value=deck["title"], max_chars=120, key=f"deck_title_{deck['id']}")
                description_value = st.text_area("Description", value=deck["description"], max_chars=1000, key=f"deck_description_{deck['id']}")
                category_value = st.text_input("Category", value=deck["category"] or "", max_chars=100, key=f"deck_category_{deck['id']}")
                save_deck = st.form_submit_button("Save deck details")
            if save_deck:
                try:
                    update_deck(deck["id"], title=title_value, description=description_value, category=category_value)
                    st.success("Deck updated.")
                    st.rerun()
                except FlashcardRepositoryError as error:
                    st.error(str(error))
            if st.checkbox("Confirm archive", key=f"deck_archive_confirm_{deck['id']}") and st.button("Archive deck", key=f"deck_archive_{deck['id']}"):
                try:
                    archive_deck(deck["id"])
                    st.rerun()
                except FlashcardRepositoryError as error:
                    st.error(str(error))
            if deck["source_filename"]:
                st.caption(f"Source PDF snapshot: {deck['source_filename']}" + (" · original file removed" if deck["source_document_id"] is None else ""))


def _render_card_browser() -> None:
    st.subheader("Browse and edit cards")
    decks = _active_decks()
    include_archived = st.checkbox("Include archived cards and decks", key="flashcard_show_archived_cards")
    if not decks and not include_archived:
        st.info("Create a deck to start adding cards.")
        return
    deck_options = [None, *(deck["id"] for deck in decks)]
    deck_by_id = {deck["id"]: deck for deck in decks}
    if st.session_state.get("flashcard_cards_deck") not in deck_options:
        st.session_state["flashcard_cards_deck"] = None
    selected_deck = st.selectbox("Deck filter", deck_options, format_func=lambda value: "All active decks" if value is None else deck_by_id[value]["title"], key="flashcard_cards_deck") if decks else None
    search = st.text_input("Search cards", key="flashcard_cards_search")
    cards = _safe_load(list_cards, deck_id=selected_deck, query=search, include_archived=include_archived) or []
    if not cards:
        st.caption("No cards match this selection.")
        return
    for item in cards:
        archived = bool(item["archived_at"])
        with st.expander(f"{item['front'][:100]} · {item['deck_title']}" + (" · Archived" if archived else "")):
            st.write("**Back:** " + item["back"])
            st.caption(f"{item['topic'] or 'No topic'} · {item['review_count']} reviews · next due {item['due_at']}")
            if item["source_filename"]:
                pages = ", ".join(str(page) for page in item["source_pages"])
                st.caption(f"Source: {item['source_filename']}" + (f" · page(s) {pages}" if pages else "") + (" · original PDF removed" if item["source_document_id"] is None else ""))
            if archived:
                if st.button("Restore card", key=f"flashcard_restore_{item['id']}"):
                    try:
                        archive_card(item["id"], archived=False)
                        st.rerun()
                    except FlashcardRepositoryError as error:
                        st.error(str(error))
                continue
            if get_deck(item["deck_id"], include_archived=False) is None:
                st.info("Restore the archived deck before editing this card.")
                continue
            with st.form(f"flashcard_edit_{item['id']}"):
                front = st.text_area("Front", value=item["front"], max_chars=500, key=f"card_front_{item['id']}")
                back = st.text_area("Back", value=item["back"], max_chars=5000, key=f"card_back_{item['id']}")
                explanation = st.text_area("Explanation", value=item["explanation"], max_chars=3000, key=f"card_explanation_{item['id']}")
                topic = st.text_input("Topic", value=item["topic"] or "", max_chars=120, key=f"card_topic_{item['id']}")
                destination = st.selectbox("Move to deck", [deck["id"] for deck in decks], index=next((i for i, deck in enumerate(decks) if deck["id"] == item["deck_id"]), 0), format_func=lambda value: deck_by_id[value]["title"], key=f"card_destination_{item['id']}")
                update = st.form_submit_button("Save card")
            if update:
                try:
                    update_card(
                        item["id"], destination, front=front, back=back, explanation=explanation,
                        topic=topic, difficulty=item["difficulty"], source_document_id=item["source_document_id"],
                        source_filename=item["source_filename"], source_pages=item["source_pages"],
                    )
                    st.success("Card updated; its review history remains attached.")
                    st.rerun()
                except FlashcardRepositoryError as error:
                    st.error(str(error))
            if st.checkbox("Confirm archive", key=f"card_archive_confirm_{item['id']}") and st.button("Archive card", key=f"card_archive_{item['id']}"):
                try:
                    archive_card(item["id"])
                    st.rerun()
                except FlashcardRepositoryError as error:
                    st.error(str(error))


def _render_manual_creation() -> None:
    st.subheader("Create a card manually")
    decks = _active_decks()
    if not decks:
        st.info("Create a deck first. Manual card creation is available without a Gemini API key.")
        return
    deck_by_id = {deck["id"]: deck for deck in decks}
    try:
        documents = list_documents()
    except DocumentError as error:
        documents = []
        st.error(str(error))
    doc_by_id = {item["id"]: item for item in documents}
    doc_options = [None, *doc_by_id]
    if st.session_state.get("flashcard_manual_source") not in doc_options:
        st.session_state["flashcard_manual_source"] = None
    source_id = st.selectbox(
        "Source PDF (optional)", doc_options,
        format_func=lambda value: "No source PDF" if value is None else doc_by_id[value]["original_filename"],
        key="flashcard_manual_source",
    )
    source_pages = []
    if source_id:
        page_count = doc_by_id[source_id]["page_count"]
        if int(st.session_state.get("flashcard_manual_source_page", 0) or 0) > page_count:
            st.session_state["flashcard_manual_source_page"] = 0
        page = st.number_input(
            "Source page (optional; 0 means none)", min_value=0, max_value=page_count,
            value=0, step=1, key="flashcard_manual_source_page",
        )
        source_pages = [int(page)] if page else []
    with st.form("flashcard_manual_create"):
        deck_id = st.selectbox("Deck", list(deck_by_id), format_func=lambda value: deck_by_id[value]["title"])
        front = st.text_area("Front / question", max_chars=500)
        back = st.text_area("Back / answer", max_chars=5000)
        explanation = st.text_area("Explanation (optional)", max_chars=3000)
        topic = st.text_input("Topic (optional)", max_chars=120)
        difficulty = st.selectbox("Difficulty (optional)", ["Not set", "Basic", "Intermediate", "Advanced"])
        save = st.form_submit_button("Create flashcard", type="primary")
    if save:
        try:
            create_card(
                deck_id, front=front, back=back, explanation=explanation, topic=topic,
                difficulty=None if difficulty == "Not set" else difficulty,
                source_document_id=source_id,
                source_filename=doc_by_id[source_id]["original_filename"] if source_id else None,
                source_pages=source_pages,
            )
            st.success("Flashcard created. It is ready for your next review session.")
            st.rerun()
        except FlashcardRepositoryError as error:
            st.error(str(error))


def _render_generated_proposals() -> None:
    proposals = st.session_state.get("flashcard_generated_proposals")
    if not proposals:
        return
    st.divider()
    st.subheader("Review generated cards before saving")
    st.caption("Nothing is stored until you select cards and confirm. You can edit or discard each proposal.")
    decks = _active_decks()
    if not decks:
        st.warning("Create an active deck before saving these proposals.")
        return
    deck_map = {deck["id"]: deck["title"] for deck in decks}
    default_deck = st.session_state.get("flashcard_generated_target")
    options = list(deck_map)
    if default_deck not in options:
        default_deck = options[0]
    target_deck = st.selectbox("Save selected cards to", options, index=options.index(default_deck), format_func=lambda value: deck_map[value], key="flashcard_proposal_target")
    selected_cards = []
    for index, proposal in enumerate(proposals):
        card_id = proposal["card_id"]
        with st.expander(f"Proposal {index + 1} · {proposal.get('topic') or 'Topic not classified'}", expanded=index == 0):
            keep = st.checkbox("Save this card", value=True, key=f"proposal_keep_{card_id}")
            front = st.text_area("Front", value=proposal["front"], max_chars=500, key=f"proposal_front_{card_id}")
            back = st.text_area("Back", value=proposal["back"], max_chars=5000, key=f"proposal_back_{card_id}")
            explanation = st.text_area("Explanation", value=proposal.get("explanation", ""), max_chars=3000, key=f"proposal_explanation_{card_id}")
            topic = st.text_input("Topic", value=proposal.get("topic") or "", max_chars=120, key=f"proposal_topic_{card_id}")
            st.caption(f"Supported PDF page(s): {', '.join(map(str, proposal.get('source_pages', []))) or 'Not available'}")
            if keep:
                selected_cards.append({
                    **proposal,
                    "front": front,
                    "back": back,
                    "explanation": explanation,
                    "topic": topic,
                    "source_filename": st.session_state.get("flashcard_generated_filename"),
                })
    save_col, discard_col = st.columns(2)
    if save_col.button(f"Save {len(selected_cards)} selected card(s)", type="primary", disabled=not selected_cards, key="flashcard_save_proposals"):
        try:
            create_cards_batch(target_deck, selected_cards)
            st.session_state.pop("flashcard_generated_proposals", None)
            st.session_state.pop("flashcard_generated_filename", None)
            st.success(f"Saved {len(selected_cards)} flashcard(s).")
            st.rerun()
        except FlashcardRepositoryError as error:
            st.error(str(error))
    if discard_col.button("Discard all proposals", key="flashcard_discard_proposals"):
        st.session_state.pop("flashcard_generated_proposals", None)
        st.session_state.pop("flashcard_generated_filename", None)
        st.rerun()


def _render_generate_creation() -> None:
    st.subheader("Generate cards from a PDF")
    decks = _active_decks()
    if not decks:
        st.info("Create a deck before generating cards. Generated cards always require your review before saving.")
        return
    document = render_document_picker("Study source PDF", key="flashcards")
    if document is None:
        return
    if not document.get("text_ready", document["extraction_status"] == "ready") or not document["extracted_text"].strip():
        st.warning("This PDF has no usable native or OCR text for generation. Run local OCR from My Documents if scanned pages were detected.")
        return
    deck_titles = {deck["id"]: deck["title"] for deck in decks}
    with st.form("flashcard_generate_form"):
        target_deck = st.selectbox("Target deck", list(deck_titles), format_func=lambda value: deck_titles[value])
        count = st.slider("Number of cards", 5, 30, 10)
        complexity = st.selectbox("Complexity", ["Basic", "Intermediate", "Advanced", "Mixed"])
        style = st.selectbox("Card style", ["Definitions", "Concepts", "Processes", "Comparisons", "Mixed"])
        topic_focus = st.text_input("Topic or chapter focus (optional)", max_chars=120)
        generate = st.form_submit_button("Generate proposals", type="primary", disabled=not is_gemini_available_for_session())
    st.caption(f"Model: {load_gemini_model()} · Generation uses retrieved passages when this PDF is indexed; otherwise a bounded text excerpt.")
    if not is_gemini_available_for_session():
        st.info("Generated cards need a Gemini key. You can still create and review manual cards without one.")
    if generate:
        try:
            with st.spinner("Drafting active-recall cards from your source…"):
                result = generate_document_flashcards(
                    document,
                    count=count,
                    complexity=complexity,
                    style=style,
                    topic_focus=topic_focus,
                    gemini_service=get_gemini_service_for_session(),
                )
            st.session_state["flashcard_generated_proposals"] = result.cards
            st.session_state["flashcard_generated_target"] = target_deck
            st.session_state["flashcard_generated_filename"] = document["original_filename"]
            if len(result.cards) < count:
                st.session_state["flashcard_generated_shortfall"] = f"Generated {len(result.cards)} valid cards from {count} requested."
            try:
                record_ai_activity("flashcards_generated", "Generated flashcard proposals")
            except (OSError, sqlite3.Error):
                st.warning("Cards were generated, but the activity could not be recorded locally.")
        except (GeminiServiceError, FlashcardValidationError, ValueError) as error:
            st.error(str(error))
    shortfall = st.session_state.pop("flashcard_generated_shortfall", None)
    if shortfall:
        st.warning(shortfall)
    _render_generated_proposals()


def _render_creation() -> None:
    manual_tab, generated_tab = st.tabs(["Manual card", "Generate from PDF"])
    with manual_tab:
        _render_manual_creation()
    with generated_tab:
        _render_generate_creation()


def _clear_session() -> None:
    for key in (
        "flashcard_session_id", "flashcard_session_queue", "flashcard_session_index",
        "flashcard_session_reviewed", "flashcard_session_revealed", "flashcard_session_source_deck",
    ):
        st.session_state.pop(key, None)


def _render_session() -> bool:
    session_id = st.session_state.get("flashcard_session_id")
    queue = st.session_state.get("flashcard_session_queue", [])
    index = int(st.session_state.get("flashcard_session_index", 0))
    reviewed = int(st.session_state.get("flashcard_session_reviewed", 0))
    if not session_id:
        return False
    st.subheader("Active recall session")
    if not queue or index >= len(queue):
        st.success(f"Session complete · {reviewed} card(s) reviewed.")
        st.caption(f"{get_flashcard_stats()['due_today']} card(s) remain due today. Again-rated cards return after 10 minutes in a later session.")
        if st.button("Finish session", key="flashcard_finish_session"):
            _clear_session()
            st.rerun()
        return True
    card = get_card(queue[index])
    if card is None:
        st.warning("A card in this session was archived or removed. Skipping it.")
        st.session_state["flashcard_session_index"] = index + 1
        st.rerun()
        return True
    st.progress((index + 1) / len(queue), text=f"Card {index + 1} of {len(queue)}")
    a, b, c = st.columns(3)
    a.metric("Reviewed this session", reviewed)
    b.metric("Remaining in session", len(queue) - index)
    c.metric("Due today", get_flashcard_stats()["due_today"])
    st.caption(f"{card['deck_title']}" + (f" · {card['topic']}" if card["topic"] else ""))
    st.markdown("### " + card["front"])
    revealed_key = "flashcard_session_revealed"
    if not st.session_state.get(revealed_key, False):
        if st.button("Show answer", type="primary", key=f"flashcard_reveal_{session_id}_{card['id']}"):
            st.session_state[revealed_key] = True
            st.rerun()
    else:
        st.markdown("### Answer")
        st.write(card["back"])
        if card["explanation"]:
            st.caption(card["explanation"])
        if card["source_filename"]:
            pages = ", ".join(map(str, card["source_pages"]))
            st.caption(f"Source: {card['source_filename']}" + (f" · page(s) {pages}" if pages else "") + (" · original PDF removed" if card["source_document_id"] is None else ""))
        st.write("How well did you recall this?")
        descriptions = {
            "again": "Repeat in 10 minutes; counts as a lapse.",
            "hard": "A short interval; counts as a successful recall.",
            "good": "A normal interval that grows with successful reviews.",
            "easy": "A longer interval than Good.",
        }
        columns = st.columns(4)
        for column, rating in zip(columns, RATINGS):
            if column.button(RATING_LABELS[rating], key=f"flashcard_rate_{session_id}_{card['id']}_{rating}", help=descriptions[rating]):
                try:
                    record_card_review(card["id"], session_id, rating, answer_revealed=True)
                    st.session_state["flashcard_session_reviewed"] = reviewed + 1
                    st.session_state["flashcard_session_index"] = index + 1
                    st.session_state[revealed_key] = False
                    st.rerun()
                except FlashcardRepositoryError as error:
                    st.error(str(error))
    if st.button("End session", key=f"flashcard_end_session_{session_id}"):
        _clear_session()
        st.rerun()
    return True


def _render_review_history() -> None:
    with st.expander("Recent review history", expanded=False):
        history = list_review_history(limit=20)
        if not history:
            st.caption("Card review events will appear here after your first rating.")
            return
        for item in history:
            st.write(
                f"{item['reviewed_at']} · **{item['deck_title']}** · {item['rating'].title()} · "
                f"{item['front'][:100]} · interval {item['new_interval_days']:.2f} day(s)"
            )
        stats = get_flashcard_stats()
        with st.container(border=True):
            st.caption("Self-rated recall ratings (not objective test scores)")
            st.write(" · ".join(f"{rating.title()}: {stats['rating_distribution'][rating]}" for rating in RATINGS))


def _render_study() -> None:
    decks = _active_decks()
    if _render_session():
        return
    if not decks:
        st.info("Create a deck and add cards to begin. Manual flashcards and review work without a Gemini key.")
        _render_review_history()
        return
    deck_map = {deck["id"]: deck["title"] for deck in decks}
    deck_options = [None, *deck_map]
    if st.session_state.get("flashcard_study_deck") not in deck_options:
        st.session_state["flashcard_study_deck"] = None
    with st.form("flashcard_start_session"):
        deck_id = st.selectbox("Study which deck?", deck_options, format_func=lambda value: "All active decks" if value is None else deck_map[value], key="flashcard_study_deck")
        include_new = st.checkbox("Include new cards", value=True)
        daily_new_limit = st.slider("New card allowance today", 0, 30, 10, disabled=not include_new)
        due_kind = st.selectbox("Review cards", ["all", "overdue", "due_today"], format_func=lambda value: {"all": "All due cards (overdue first)", "overdue": "Overdue only", "due_today": "Due today only"}[value])
        session_limit = st.slider("Maximum cards in this session", 5, 100, 30)
        start = st.form_submit_button("Start due-card session", type="primary")
    if start:
        try:
            queue = get_due_cards(
                deck_id=deck_id, include_new=include_new,
                due_kind=due_kind,
                daily_new_limit=daily_new_limit if include_new else 0,
                session_limit=session_limit,
            )
            st.session_state["flashcard_session_id"] = uuid.uuid4().hex
            st.session_state["flashcard_session_queue"] = [card["id"] for card in queue]
            st.session_state["flashcard_session_index"] = 0
            st.session_state["flashcard_session_reviewed"] = 0
            st.session_state["flashcard_session_revealed"] = False
            st.session_state["flashcard_session_source_deck"] = deck_id
            st.rerun()
        except FlashcardRepositoryError as error:
            st.error(str(error))
    due_queue = _safe_load(get_due_cards, deck_id=deck_id, include_new=False, due_kind=due_kind, session_limit=100) or []
    if not due_queue:
        stats = get_flashcard_stats()
        if stats["total_cards"]:
            st.success("Nothing is due right now. You can include a few new cards in your next session.")
        else:
            st.info("There are no cards to study yet. Add cards in the Create cards tab.")
    else:
        st.caption(f"{len(due_queue)} reviewed card(s) currently due in this selection. Start a session to review a manageable group.")
    _render_review_history()


def _render_flashcards_content() -> None:
    try:
        _render_summary()
    except Exception as error:
        st.error(f"Flashcard data could not be loaded from the local database: {error}")
        return
    if st.session_state.get("flashcard_session_id"):
        _render_session()
        return
    study_tab, deck_tab, cards_tab, create_tab = st.tabs(["Study due cards", "Decks", "Cards", "Create cards"])
    with study_tab:
        _render_study()
    with deck_tab:
        _render_decks()
    with cards_tab:
        _render_card_browser()
    with create_tab:
        _render_creation()


def render_flashcards() -> None:
    try:
        _render_flashcards_content()
    except (sqlite3.Error, OSError) as error:
        st.error(f"Flashcard data could not be saved or loaded: {error}")
