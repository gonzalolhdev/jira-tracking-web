from datetime import date

from jira_tracking_bot.balancing import rebalance_day
from jira_tracking_bot.web.schemas import DayPlan, DayPlanEntry


def _entry(issue_key: str, issue_type: str) -> DayPlanEntry:
    return DayPlanEntry(
        date=date(2026, 4, 10),
        issue_key=issue_key,
        issue_type=issue_type,
        summary=f"Summary {issue_key}",
        status="In Progress",
        minutes=0,
        source="generated",
        locked=False,
        removed=False,
    )


def test_rebalance_less_than_8_prioritizes_bugs_then_stories() -> None:
    entries = [
        _entry("BUG-1", "Bug"),
        _entry("BUG-2", "Bug"),
        _entry("BUG-3", "Bug"),
        _entry("BUG-4", "Bug"),
        _entry("STORY-1", "Story"),
    ]
    day = DayPlan(date=date(2026, 4, 10), entries=entries, total_minutes=0)

    balanced = rebalance_day(day)

    by_key = {entry.issue_key: entry.minutes for entry in balanced.entries}
    assert by_key["BUG-1"] == 60
    assert by_key["BUG-2"] == 60
    assert by_key["BUG-3"] == 60
    assert by_key["BUG-4"] == 60
    assert by_key["STORY-1"] == 240
    assert balanced.total_minutes == 480


def test_rebalance_exactly_8_assigns_one_hour_each() -> None:
    entries = [_entry(f"T-{idx}", "Bug" if idx % 2 else "Story") for idx in range(8)]
    day = DayPlan(date=date(2026, 4, 10), entries=entries, total_minutes=0)

    balanced = rebalance_day(day)

    assert all(entry.minutes == 60 for entry in balanced.entries)
    assert balanced.total_minutes == 480


def test_rebalance_more_than_8_uses_half_hour_base_then_story_remainder() -> None:
    entries = [
        _entry("BUG-1", "Bug"),
        _entry("BUG-2", "Bug"),
        _entry("BUG-3", "Bug"),
        _entry("BUG-4", "Bug"),
        _entry("BUG-5", "Bug"),
        _entry("BUG-6", "Bug"),
        _entry("STORY-1", "Story"),
        _entry("STORY-2", "Story"),
        _entry("STORY-3", "Story"),
        _entry("STORY-4", "Story"),
    ]
    day = DayPlan(date=date(2026, 4, 10), entries=entries, total_minutes=0)

    balanced = rebalance_day(day)

    by_key = {entry.issue_key: entry.minutes for entry in balanced.entries}
    assert by_key["BUG-1"] == 30
    assert by_key["BUG-2"] == 30
    assert by_key["BUG-3"] == 30
    assert by_key["BUG-4"] == 30
    assert by_key["BUG-5"] == 30
    assert by_key["BUG-6"] == 30
    assert by_key["STORY-1"] == 75
    assert by_key["STORY-2"] == 75
    assert by_key["STORY-3"] == 75
    assert by_key["STORY-4"] == 75
    assert balanced.total_minutes == 480


def test_rebalance_many_tickets_can_overflow_at_quarter_hour_floor() -> None:
    entries = [_entry(f"BUG-{idx}", "Bug") for idx in range(40)]
    day = DayPlan(date=date(2026, 4, 10), entries=entries, total_minutes=0)

    balanced = rebalance_day(day)

    assert all(entry.minutes == 15 for entry in balanced.entries)
    assert balanced.total_minutes == 600


def test_rebalance_outputs_quarter_hour_intervals() -> None:
    entries = [_entry(f"T-{idx}", "Sub-task") for idx in range(13)]
    day = DayPlan(date=date(2026, 4, 10), entries=entries, total_minutes=0)

    balanced = rebalance_day(day)

    assert all(entry.minutes % 15 == 0 for entry in balanced.entries)


def test_rebalance_does_not_auto_assign_spike_story() -> None:
    bug = _entry("BUG-1", "Bug")
    spike_story = DayPlanEntry(
        date=date(2026, 4, 10),
        issue_key="STORY-SPIKE",
        issue_type="Story",
        summary="SPIKE investigate integration",
        status="In Progress",
        minutes=0,
        source="generated",
        locked=False,
        removed=False,
    )
    day = DayPlan(date=date(2026, 4, 10), entries=[bug, spike_story], total_minutes=0)

    balanced = rebalance_day(day)

    by_key = {entry.issue_key: entry.minutes for entry in balanced.entries}
    assert by_key["STORY-SPIKE"] == 0
    assert by_key["BUG-1"] == 480


def test_rebalance_does_not_auto_assign_generated_secondary() -> None:
    bug = _entry("BUG-1", "Bug")
    secondary = DayPlanEntry(
        date=date(2026, 4, 10),
        issue_key="SEC-1",
        issue_type="Story",
        summary="Secondary status only",
        status="In Review",
        minutes=0,
        source="generated-secondary",
        locked=False,
        removed=False,
    )
    day = DayPlan(date=date(2026, 4, 10), entries=[bug, secondary], total_minutes=0)

    balanced = rebalance_day(day)

    by_key = {entry.issue_key: entry.minutes for entry in balanced.entries}
    assert by_key["SEC-1"] == 0
    assert by_key["BUG-1"] == 480


def test_rebalance_preserves_manual_generated_secondary_when_logged_exists() -> None:
    logged = DayPlanEntry(
        date=date(2026, 4, 10),
        issue_key="LOG-1",
        issue_type="Bug",
        summary="Already logged",
        status="Done",
        minutes=120,
        source="logged",
        locked=False,
        removed=False,
    )
    generated = _entry("GEN-1", "Story")
    generated = generated.model_copy(update={"minutes": 240})
    secondary = DayPlanEntry(
        date=date(2026, 4, 10),
        issue_key="SEC-1",
        issue_type="Story",
        summary="Secondary status only",
        status="In Review",
        minutes=30,
        source="generated-secondary",
        locked=False,
        removed=False,
    )

    day = DayPlan(date=date(2026, 4, 10), entries=[logged, generated, secondary], total_minutes=0)
    balanced = rebalance_day(day)

    by_key = {entry.issue_key: entry.minutes for entry in balanced.entries}
    assert by_key["LOG-1"] == 120
    assert by_key["GEN-1"] == 0
    assert by_key["SEC-1"] == 30
