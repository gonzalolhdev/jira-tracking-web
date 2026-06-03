from __future__ import annotations

import re

from jira_tracking_bot.web.schemas import DayPlan, DayPlanEntry


DAILY_TARGET_MINUTES = 8 * 60
QUARTER_HOUR_MINUTES = 15


def _is_bug(entry: DayPlanEntry) -> bool:
    return "bug" in entry.issue_type.lower()


def _is_story(entry: DayPlanEntry) -> bool:
    return "story" in entry.issue_type.lower()


def _is_spike_story(entry: DayPlanEntry) -> bool:
    return _is_story(entry) and bool(re.search(r"\bSPIKE\b", entry.summary or ""))


def _is_secondary_generated(entry: DayPlanEntry) -> bool:
    return entry.source == "generated-secondary"


def _round_to_quarter_hour(minutes: int) -> int:
    if minutes <= 0:
        return 0
    return int(round(minutes / QUARTER_HOUR_MINUTES) * QUARTER_HOUR_MINUTES)


def _spread_quarter_remainder(entries: list[DayPlanEntry], assigned: dict[int, int], remainder: int) -> None:
    if remainder <= 0 or not entries:
        return

    # Spread remaining quarter-hour chunks in round-robin order.
    idx = 0
    while remainder >= QUARTER_HOUR_MINUTES:
        entry = entries[idx % len(entries)]
        assigned[id(entry)] += QUARTER_HOUR_MINUTES
        remainder -= QUARTER_HOUR_MINUTES
        idx += 1


def rebalance_day(day: DayPlan) -> DayPlan:
    active_entries = [entry for entry in day.entries if not entry.removed]
    if not active_entries:
        return _with_total(day)

    # If any active entry was already logged via Tempo, skip rebalancing this day.
    # Zero out all non-logged/non-secondary-generated entries so the day isn't
    # over-assigned. Secondary-generated entries are intentionally preserved as-is
    # (default 0 unless user edits them).
    if any(entry.source == "logged" for entry in active_entries):
        updated = [
            entry if (entry.removed or entry.source == "logged" or _is_secondary_generated(entry))
            else entry.model_copy(update={"minutes": 0})
            for entry in day.entries
        ]
        return _with_total(day.model_copy(update={"entries": updated}))

    spike_stories = [entry for entry in active_entries if _is_spike_story(entry)]
    spike_story_ids = {id(entry) for entry in spike_stories}
    secondary_generated = [entry for entry in active_entries if _is_secondary_generated(entry)]
    secondary_generated_ids = {id(entry) for entry in secondary_generated}
    allocatable_entries = [
        entry
        for entry in active_entries
        if not _is_spike_story(entry) and not _is_secondary_generated(entry)
    ]
    if not allocatable_entries:
        return _with_total(day)

    bugs = [entry for entry in allocatable_entries if _is_bug(entry)]
    stories = [entry for entry in allocatable_entries if _is_story(entry)]
    non_bugs = [entry for entry in allocatable_entries if not _is_bug(entry)]

    assigned: dict[int, int] = {id(entry): 0 for entry in allocatable_entries}
    count = len(allocatable_entries)

    if count < 8:
        for bug in bugs:
            assigned[id(bug)] = 60

        remaining = DAILY_TARGET_MINUTES - sum(assigned.values())
        if remaining > 0:
            # Prioritize stories for the remainder. If none, use other non-bug tickets, then bugs.
            pool = stories or non_bugs or bugs
            _spread_quarter_remainder(pool, assigned, remaining)

    elif count == 8:
        for entry in allocatable_entries:
            assigned[id(entry)] = 60

    else:
        base = 30
        if count * base > DAILY_TARGET_MINUTES:
            base = 15

        for entry in allocatable_entries:
            assigned[id(entry)] = base

        total_base = count * base
        if total_base < DAILY_TARGET_MINUTES:
            remaining = DAILY_TARGET_MINUTES - total_base
            pool = stories or non_bugs or bugs
            _spread_quarter_remainder(pool, assigned, remaining)
        # If total_base > DAILY_TARGET_MINUTES (many tickets with 0.25h base), keep overflow as requested.

    new_minutes = [_round_to_quarter_hour(assigned[id(entry)]) for entry in allocatable_entries]

    updated_entries: list[DayPlanEntry] = []
    active_index = 0
    for entry in day.entries:
        if entry.removed or id(entry) in spike_story_ids or id(entry) in secondary_generated_ids:
            updated_entries.append(entry)
            continue
        updated_entries.append(entry.model_copy(update={"minutes": new_minutes[active_index]}))
        active_index += 1

    return _with_total(day.model_copy(update={"entries": updated_entries}))


def rebalance_month(days: list[DayPlan]) -> list[DayPlan]:
    return [rebalance_day(day) for day in days]


def _with_total(day: DayPlan) -> DayPlan:
    total = sum(entry.minutes for entry in day.entries if not entry.removed)
    return day.model_copy(update={"total_minutes": total})
