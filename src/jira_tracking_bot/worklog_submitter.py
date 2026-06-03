from __future__ import annotations

from datetime import datetime

from jira_tracking_bot.jira_client import JiraClient, JiraClientError
from jira_tracking_bot.models import Issue, SubmissionResult, WorklogEntry



def submit_worklogs(
    *,
    client: JiraClient,
    issues: list[Issue],
    minutes_by_issue: dict[str, int],
    started_at: datetime,
    comment: str | None,
) -> list[SubmissionResult]:
    results: list[SubmissionResult] = []

    issue_map = {issue.key: issue for issue in issues}
    for key, minutes in minutes_by_issue.items():
        if minutes <= 0:
            continue

        issue = issue_map.get(key)
        if not issue:
            results.append(
                SubmissionResult(issue_key=key, minutes=minutes, success=False, message="Issue not found")
            )
            continue

        entry = WorklogEntry(issue=issue, time_minutes=minutes, comment=comment, started_at=started_at)

        try:
            client.create_worklog(entry)
            results.append(
                SubmissionResult(issue_key=key, minutes=minutes, success=True, message="submitted")
            )
        except JiraClientError as exc:
            results.append(
                SubmissionResult(issue_key=key, minutes=minutes, success=False, message=str(exc))
            )

    return results
