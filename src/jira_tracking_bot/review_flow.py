from __future__ import annotations

from typing import Iterable

from rich.console import Console
from rich.prompt import Confirm, Prompt

from jira_tracking_bot.models import Issue



def collect_final_minutes(issues: Iterable[Issue], suggested_minutes: dict[str, int]) -> dict[str, int]:
    console = Console()
    final_minutes: dict[str, int] = {}

    for issue in issues:
        suggested = suggested_minutes.get(issue.key, 0)
        label = f"{issue.key} minutes"
        raw = Prompt.ask(label, default=str(suggested))
        try:
            value = int(raw)
        except ValueError:
            console.print(f"Invalid minutes '{raw}', defaulting to {suggested}")
            value = suggested

        final_minutes[issue.key] = max(0, value)

    return final_minutes



def ask_for_confirmation(total_minutes: int, period: str) -> bool:
    return Confirm.ask(
        f"Submit {total_minutes} minutes of worklogs for period '{period}'?",
        default=False,
    )
