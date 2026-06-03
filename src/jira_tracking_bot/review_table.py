from __future__ import annotations

from rich.console import Console
from rich.table import Table

from jira_tracking_bot.models import Issue



def render_preview_table(
    *,
    issues: list[Issue],
    suggested_minutes: dict[str, int],
    final_minutes: dict[str, int],
    sources: dict[str, str],
) -> None:
    table = Table(title="Jira Worklog Preview")
    table.add_column("Type")
    table.add_column("Ticket")
    table.add_column("Summary")
    table.add_column("Suggested")
    table.add_column("Final")
    table.add_column("Source")

    for issue in issues:
        suggested = suggested_minutes.get(issue.key, 0)
        final = final_minutes.get(issue.key, suggested)
        table.add_row(
            issue.issue_type,
            issue.key,
            issue.summary,
            _minutes_to_human(suggested),
            _minutes_to_human(final),
            sources.get(issue.key, "manual"),
        )

    Console().print(table)



def _minutes_to_human(minutes: int) -> str:
    if minutes <= 0:
        return "0m"
    hours, rem = divmod(minutes, 60)
    if hours and rem:
        return f"{hours}h {rem}m"
    if hours:
        return f"{hours}h"
    return f"{rem}m"
