from datetime import datetime
from zoneinfo import ZoneInfo

from jira_tracking_bot.config import AppConfig
from jira_tracking_bot.discovery import build_jql_for_day_snapshot, build_jql_for_window, build_period_window



def test_build_period_window_week_starts_monday() -> None:
    now = datetime(2026, 4, 8, 10, 30, tzinfo=ZoneInfo("Europe/Madrid"))  # Wednesday
    start, end = build_period_window("week", now)

    assert start.year == 2026
    assert start.month == 4
    assert start.day == 6  # Monday
    assert start.hour == 0
    assert end == now



def test_build_jql_includes_project_when_configured() -> None:
    config = AppConfig(
        jira_base_url="https://example.atlassian.net",
        timezone="Europe/Madrid",
        default_projects=["PROJ", "OPS", "PLAT"],
    )

    start = datetime(2026, 4, 1, 0, 0, tzinfo=ZoneInfo("UTC"))
    end = datetime(2026, 4, 6, 12, 0, tzinfo=ZoneInfo("UTC"))

    jql = build_jql_for_window(config, start, end)

    assert "worklogAuthor = currentUser()" in jql
    assert "project in (\"PROJ\", \"OPS\", \"PLAT\")" in jql
    assert "worklogDate >= \"2026-04-01\"" in jql
    assert "worklogDate <= \"2026-04-06\"" in jql


def test_build_jql_for_day_snapshot_includes_status_and_assignee_history() -> None:
    config = AppConfig(
        jira_base_url="https://example.atlassian.net",
        timezone="Europe/Madrid",
        default_projects=["PROJ"],
        in_progress_statuses=["In Progress", "Code Review"],
    )

    jql = build_jql_for_day_snapshot(config, datetime(2026, 4, 1, 9, 0, tzinfo=ZoneInfo("UTC")).date())

    assert 'assignee WAS IN (currentUser()) ON "2026-04-01"' in jql
    assert 'status WAS IN ("In Progress", "Code Review") ON "2026-04-01"' in jql
    assert 'project in ("PROJ")' in jql
