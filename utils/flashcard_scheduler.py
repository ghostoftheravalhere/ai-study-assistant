"""Deterministic, transparent SM-2-inspired flashcard interval scheduling."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping


RATINGS = ("again", "hard", "good", "easy")
RATING_LABELS = {
    "again": "Again · I could not recall it",
    "hard": "Hard · I recalled it with difficulty",
    "good": "Good · I recalled it with normal effort",
    "easy": "Easy · I recalled it effortlessly",
}
AGAIN_MINUTES = 10
MAX_INTERVAL_DAYS = 3650


@dataclass(frozen=True)
class ScheduleResult:
    reviewed_at: str
    due_at: str
    previous_interval_days: float
    interval_days: float
    ease_factor: float
    review_count: int
    successful_recall_count: int
    lapse_count: int
    learning_state: str
    recall_successful: bool


def _aware(value: datetime) -> datetime:
    return value.astimezone() if value.tzinfo is None else value


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def schedule_review(
    state: Mapping[str, object],
    rating: str,
    *,
    now: datetime | None = None,
) -> ScheduleResult:
    """Calculate the next review without I/O; calendar intervals use app-local time.

    AGAIN repeats after 10 minutes and counts as a lapse. First HARD/GOOD/EASY
    intervals are 1/2/4 days. During relearning after AGAIN, those ratings use
    1/3/4 days. Later HARD advances by about 1.2x, GOOD multiplies the interval
    by ease, and EASY schedules at least one day beyond the corresponding GOOD
    interval. HARD slightly lowers ease, EASY raises it,
    and all day intervals are clamped to 1..3650. A failed recall is not put
    back into the current session; it becomes due in 10 minutes for a later one.
    """
    normalized_rating = rating.casefold() if isinstance(rating, str) else ""
    if normalized_rating not in RATINGS:
        raise ValueError("Choose Again, Hard, Good, or Easy.")
    current = _aware(now or datetime.now().astimezone())
    review_count = int(state.get("review_count", 0) or 0)
    successes = int(state.get("successful_recall_count", 0) or 0)
    lapses = int(state.get("lapse_count", 0) or 0)
    previous_interval = max(0.0, float(state.get("interval_days", 0) or 0))
    ease = max(1.3, float(state.get("ease_factor", 2.5) or 2.5))
    state_before = state.get("learning_state")
    is_new = review_count == 0 or state_before == "new"
    is_learning = state_before == "learning"

    if normalized_rating == "again":
        interval = AGAIN_MINUTES / (24 * 60)
        next_due = current + timedelta(minutes=AGAIN_MINUTES)
        ease = max(1.3, ease - 0.2)
        lapses += 1
        learning_state = "learning"
        successful = False
    else:
        if normalized_rating == "hard":
            interval = 1.0 if is_new or is_learning else max(1.0, round(previous_interval * 1.2))
            ease = max(1.3, ease - 0.15)
        elif normalized_rating == "good":
            interval = 2.0 if is_new else (3.0 if is_learning or previous_interval <= 1 else max(1, round(previous_interval * ease)))
        else:  # easy
            if is_new or is_learning:
                interval = 4.0
            else:
                good_interval = 3.0 if previous_interval <= 1 else max(1, round(previous_interval * ease))
                interval = max(good_interval + 1, round(previous_interval * ease * 1.3))
            ease += 0.15
        interval = float(max(1, min(MAX_INTERVAL_DAYS, interval)))
        local_current = current.astimezone()
        next_local = datetime.combine(
            local_current.date() + timedelta(days=int(interval)),
            local_current.timetz().replace(tzinfo=None),
        ).astimezone()
        next_due = next_local.astimezone(timezone.utc)
        learning_state = "review"
        successes += 1
        successful = True

    return ScheduleResult(
        reviewed_at=_timestamp(current),
        due_at=_timestamp(next_due),
        previous_interval_days=previous_interval,
        interval_days=interval,
        ease_factor=round(ease, 2),
        review_count=review_count + 1,
        successful_recall_count=successes,
        lapse_count=lapses,
        learning_state=learning_state,
        recall_successful=successful,
    )
