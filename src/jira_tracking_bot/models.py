from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Issue:
    key: str
    issue_type: str
    summary: str
    status: str = "Unknown"
    issue_id: str | None = None


@dataclass(slots=True)
class WorklogEntry:
    issue: Issue
    time_minutes: int
    comment: str | None
    started_at: datetime


@dataclass(slots=True)
class SubmissionResult:
    issue_key: str
    minutes: int
    success: bool
    message: str
