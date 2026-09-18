from __future__ import annotations

from datetime import date, datetime, time, timedelta

from jira_tracking_bot.config import AppConfig


def build_period_window(period: str, tz_now: datetime | None = None) -> tuple[datetime, datetime]:
    if period not in {"day", "week", "month"}:
        raise ValueError("period must be one of: day, week, month")

    now = tz_now or datetime.now()

    if period == "day":
        start = datetime.combine(now.date(), time.min, now.tzinfo)
    elif period == "week":
        start_date = now.date() - timedelta(days=now.date().weekday())
        start = datetime.combine(start_date, time.min, now.tzinfo)
    else:
        start_date = date(year=now.year, month=now.month, day=1)
        start = datetime.combine(start_date, time.min, now.tzinfo)

    end = now
    return start, end



def build_jql_for_window(config: AppConfig, start: datetime, end: datetime) -> str:
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")
    clauses: list[str] = [
        "worklogAuthor = currentUser()",
        f'worklogDate >= "{start_str}"',
        f'worklogDate <= "{end_str}"',
    ]

    if config.default_projects:
        quoted = ", ".join(f'"{project}"' for project in config.default_projects)
        clauses.append(f"project in ({quoted})")

    return " AND ".join(clauses) + " ORDER BY updated DESC"


def build_jql_for_day_snapshot(config: AppConfig, day: date) -> str:
    day_str, day_start_str, next_day_start_str = _build_day_jql_boundaries(day)
    mode = _normalize_daily_snapshot_mode(config.daily_snapshot_mode)
    statuses = config.in_progress_statuses or ["In Progress"]
    quoted_statuses = ", ".join(_quote_jql_literal(status) for status in statuses)

    if mode == "bounded_during":
        clauses: list[str] = [
            f'assignee WAS IN (currentUser()) DURING ("{day_start_str}", "{next_day_start_str}")',
            f'status WAS IN ({quoted_statuses}) DURING ("{day_start_str}", "{next_day_start_str}")',
        ]
    else:
        clauses = [
            f'assignee WAS IN (currentUser()) ON "{day_str}"',
            f'status WAS IN ({quoted_statuses}) ON "{day_str}"',
        ]

    if config.default_projects:
        quoted = ", ".join(_quote_jql_literal(project) for project in config.default_projects)
        clauses.append(f"project in ({quoted})")

    clauses.append(f'(resolution is EMPTY OR resolved >= "{day_str}")')

    return " AND ".join(clauses) + " ORDER BY updated DESC"


def build_jql_for_day_secondary_snapshot(config: AppConfig, day: date) -> str | None:
    """Build a JQL query for secondary tracking statuses (e.g. 'In Review').

    Returns None when no secondary statuses are configured so callers can skip
    the query entirely rather than making a no-op search request.
    """
    if not config.secondary_tracking_statuses:
        return None

    day_str, day_start_str, next_day_start_str = _build_day_jql_boundaries(day)
    mode = _normalize_daily_snapshot_mode(config.daily_snapshot_mode)
    quoted_statuses = ", ".join(_quote_jql_literal(s) for s in config.secondary_tracking_statuses)

    if mode == "bounded_during":
        clauses: list[str] = [
            f'assignee WAS IN (currentUser()) DURING ("{day_start_str}", "{next_day_start_str}")',
            f'status WAS IN ({quoted_statuses}) DURING ("{day_start_str}", "{next_day_start_str}")',
        ]
    else:
        clauses = [
            f'assignee WAS IN (currentUser()) ON "{day_str}"',
            f'status WAS IN ({quoted_statuses}) ON "{day_str}"',
        ]

    if config.default_projects:
        quoted = ", ".join(_quote_jql_literal(project) for project in config.default_projects)
        clauses.append(f"project in ({quoted})")

    clauses.append(f'(resolution is EMPTY OR resolved >= "{day_str}")')

    return " AND ".join(clauses) + " ORDER BY updated DESC"


def build_jql_for_month_worklog_tickets(config: AppConfig, start_day: date, end_day: date) -> str:
    """Build JQL for issues with user worklogs inside a month date range."""
    start_str = start_day.strftime("%Y-%m-%d")
    end_str = end_day.strftime("%Y-%m-%d")

    clauses: list[str] = [
        "worklogAuthor = currentUser()",
        f'worklogDate >= "{start_str}"',
        f'worklogDate <= "{end_str}"',
    ]

    if config.default_projects:
        quoted = ", ".join(_quote_jql_literal(project) for project in config.default_projects)
        clauses.append(f"project in ({quoted})")

    return " AND ".join(clauses) + " ORDER BY updated DESC"


def _quote_jql_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_day_jql_boundaries(day: date) -> tuple[str, str, str]:
    day_str = day.strftime("%Y-%m-%d")
    next_day = day + timedelta(days=1)
    next_day_str = next_day.strftime("%Y-%m-%d")
    return day_str, f"{day_str} 00:00", f"{next_day_str} 00:00"


def _normalize_daily_snapshot_mode(mode: str | None) -> str:
    if not mode:
        return "on_day"
    normalized = mode.strip().lower()
    if normalized in {"on_day", "bounded_during"}:
        return normalized
    return "on_day"
