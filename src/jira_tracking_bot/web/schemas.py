from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class DayPlanEntry(BaseModel):
    date: date
    issue_key: str
    issue_type: str
    summary: str
    status: str
    minutes: int = 0
    source: str = "generated"
    locked: bool = False
    removed: bool = False
    worklog_id: int | None = None


class DayPlan(BaseModel):
    date: date
    entries: list[DayPlanEntry] = Field(default_factory=list)
    total_minutes: int = 0


class MonthPlanResponse(BaseModel):
    month: str
    timezone: str
    days: list[DayPlan]


class RebalanceRequest(BaseModel):
    month: str
    timezone: str
    days: list[DayPlan]


class SubmitRequest(BaseModel):
    month: str
    timezone: str
    days: list[DayPlan]
    comment: str | None = None


class SubmitItemResult(BaseModel):
    date: date
    issue_key: str
    minutes: int
    success: bool
    message: str


class SubmitResponse(BaseModel):
    results: list[SubmitItemResult]


class DayRefreshRequest(BaseModel):
    date: str  # ISO format YYYY-MM-DD
    timezone: str | None = None


class DayAddTicketRequest(BaseModel):
    date: str  # ISO format YYYY-MM-DD
    issue_key: str
    timezone: str | None = None


class DayAddTicketRequest(BaseModel):
    date: str  # ISO format YYYY-MM-DD
    issue_key: str
    timezone: str | None = None
