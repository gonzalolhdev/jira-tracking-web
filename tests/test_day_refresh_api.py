from __future__ import annotations

import logging
from pathlib import Path
import re

from fastapi.testclient import TestClient

from jira_tracking_bot.config import AppConfig
from jira_tracking_bot.models import Issue
from jira_tracking_bot.web import app as web_app


class _FakeJiraClientNoLogs:
    def __init__(self, _config: AppConfig) -> None:
        pass

    def validate_auth(self) -> None:
        return None

    def search_issues(self, *, jql: str, max_results: int = 100) -> list[Issue]:
        _ = max_results
        issues = {
            "PROJ-100": Issue(key="PROJ-100", issue_type="Bug", summary="Bug issue", status="In Progress"),
            "PROJ-101": Issue(key="PROJ-101", issue_type="Story", summary="Story issue", status="In Progress"),
        }
        match = re.search(r'key\s*=\s*"([A-Za-z0-9_-]+)"', jql)
        if match:
            issue = issues.get(match.group(1).upper())
            return [issue] if issue else []
        return [issues["PROJ-100"], issues["PROJ-101"]]

    def get_current_user_account_id(self) -> str:
        return "user-123"

    def get_issue_worklogs(self, issue_key: str) -> list[dict]:
        _ = issue_key
        return []

    def get_tempo_worklogs_for_user(self, *, account_id: str, from_date, to_date) -> list[dict]:
        _ = (account_id, from_date, to_date)
        return []

    def close(self) -> None:
        return None


class _FakeJiraClientWithLogs(_FakeJiraClientNoLogs):
    def get_issue_worklogs(self, issue_key: str) -> list[dict]:
        if issue_key == "PROJ-100":
            return [
                {
                    "id": "5001",
                    "author": {"accountId": "user-123"},
                    "started": "2026-04-02T09:00:00.000-0400",
                    "timeSpentSeconds": 3600,
                }
            ]
        return []


class _FakeJiraClientMonthPrefetch(_FakeJiraClientNoLogs):
    search_issue_jql_calls: list[str] = []
    search_page_calls: list[tuple[str, int, int]] = []

    @classmethod
    def reset(cls) -> None:
        cls.search_issue_jql_calls = []
        cls.search_page_calls = []

    def search_issues_page(self, *, jql: str, max_results: int = 100, start_at: int = 0) -> tuple[list[Issue], int]:
        self.__class__.search_page_calls.append((jql, max_results, start_at))
        return (
            [
                Issue(
                    key="PROJ-LOG",
                    issue_type="Story",
                    summary="Already logged",
                    status="Done",
                )
            ],
            1,
        )

    def search_issues(self, *, jql: str, max_results: int = 100) -> list[Issue]:
        _ = max_results
        self.__class__.search_issue_jql_calls.append(jql)
        return []

    def get_issue_worklogs(self, issue_key: str) -> list[dict]:
        if issue_key == "PROJ-LOG":
            return [
                {
                    "id": "8001",
                    "author": {"accountId": "user-123"},
                    "started": "2026-04-01T09:00:00.000-0400",
                    "timeSpentSeconds": 8 * 60 * 60,
                }
            ]
        return []

    def get_tempo_worklogs_for_user(self, *, account_id: str, from_date, to_date) -> list[dict]:
        _ = (account_id, from_date, to_date)
        return []


class _FakeJiraClientMonthPrefetchTruncated(_FakeJiraClientMonthPrefetch):
    def search_issues_page(self, *, jql: str, max_results: int = 100, start_at: int = 0) -> tuple[list[Issue], int]:
        self.__class__.search_page_calls.append((jql, max_results, start_at))
        return (
            [
                Issue(
                    key="PROJ-LOG",
                    issue_type="Story",
                    summary="Already logged",
                    status="Done",
                )
            ],
            150,
        )


class _FakeJiraClientRefreshForce(_FakeJiraClientNoLogs):
    search_issue_jql_calls: list[str] = []

    @classmethod
    def reset(cls) -> None:
        cls.search_issue_jql_calls = []

    def search_issues(self, *, jql: str, max_results: int = 100) -> list[Issue]:
        _ = max_results
        self.__class__.search_issue_jql_calls.append(jql)
        if "status WAS IN (\"In Progress\")" in jql:
            return [
                Issue(
                    key="PROJ-100",
                    issue_type="Bug",
                    summary="Primary issue",
                    status="In Progress",
                )
            ]
        if "status WAS IN (\"In Review\")" in jql:
            return [
                Issue(
                    key="PROJ-200",
                    issue_type="Story",
                    summary="Secondary issue",
                    status="In Review",
                )
            ]
        return []

    def search_issues_page(self, *, jql: str, max_results: int = 100, start_at: int = 0) -> tuple[list[Issue], int]:
        _ = (jql, max_results, start_at)
        return ([], 0)

    def get_issue_worklogs(self, issue_key: str) -> list[dict]:
        if issue_key == "PROJ-100":
            return [
                {
                    "id": "9001",
                    "author": {"accountId": "user-123"},
                    "started": "2026-04-02T09:00:00.000-0400",
                    "timeSpentSeconds": 8 * 60 * 60,
                }
            ]
        return []


def _fake_config_with_secondary() -> AppConfig:
    return AppConfig(
        jira_base_url="https://jira.example.com",
        timezone="UTC",
        secondary_tracking_statuses=["In Review"],
        session_state_path=Path("/tmp/non-required-for-test.json"),
    )


def _fake_config() -> AppConfig:
    return AppConfig(
        jira_base_url="https://jira.example.com",
        timezone="UTC",
        session_state_path=Path("/tmp/non-required-for-test.json"),
    )


def test_refresh_day_rejects_weekend() -> None:
    client = TestClient(web_app.app)

    resp = client.post("/api/day/refresh", json={"date": "2026-04-04"})  # Saturday

    assert resp.status_code == 400
    assert "weekend" in resp.json()["detail"].lower()


def test_refresh_day_generates_and_rebalances_to_8h(monkeypatch) -> None:
    monkeypatch.setattr(web_app, "load_config", lambda: _fake_config())
    monkeypatch.setattr(web_app, "JiraClient", _FakeJiraClientNoLogs)

    client = TestClient(web_app.app)
    resp = client.post("/api/day/refresh", json={"date": "2026-04-02"})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["date"] == "2026-04-02"
    assert payload["total_minutes"] == 480
    assert len(payload["entries"]) == 2
    assert all(entry["source"] == "generated" for entry in payload["entries"])


def test_refresh_day_logged_time_wins_and_zeroes_generated(monkeypatch) -> None:
    monkeypatch.setattr(web_app, "load_config", lambda: _fake_config())
    monkeypatch.setattr(web_app, "JiraClient", _FakeJiraClientWithLogs)

    client = TestClient(web_app.app)
    resp = client.post("/api/day/refresh", json={"date": "2026-04-02"})

    assert resp.status_code == 200
    payload = resp.json()
    entries = {entry["issue_key"]: entry for entry in payload["entries"]}

    assert entries["PROJ-100"]["source"] == "logged"
    assert entries["PROJ-100"]["minutes"] == 60
    assert entries["PROJ-100"]["worklog_id"] == 5001
    assert entries["PROJ-101"]["source"] == "generated"
    assert entries["PROJ-101"]["minutes"] == 0


def test_add_day_ticket_returns_generated_manual_entry(monkeypatch) -> None:
    monkeypatch.setattr(web_app, "load_config", lambda: _fake_config())
    monkeypatch.setattr(web_app, "JiraClient", _FakeJiraClientNoLogs)

    client = TestClient(web_app.app)
    resp = client.post(
        "/api/day/tickets",
        json={"date": "2026-04-02", "issue_key": "PROJ-101"},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["date"] == "2026-04-02"
    assert payload["issue_key"] == "PROJ-101"
    assert payload["source"] == "generated-manual"
    assert payload["minutes"] == 0


def test_add_day_ticket_returns_logged_entry_when_worklog_exists(monkeypatch) -> None:
    monkeypatch.setattr(web_app, "load_config", lambda: _fake_config())
    monkeypatch.setattr(web_app, "JiraClient", _FakeJiraClientWithLogs)

    client = TestClient(web_app.app)
    resp = client.post(
        "/api/day/tickets",
        json={"date": "2026-04-02", "issue_key": "PROJ-100"},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["date"] == "2026-04-02"
    assert payload["issue_key"] == "PROJ-100"
    assert payload["source"] == "logged"
    assert payload["minutes"] == 60
    assert payload["worklog_id"] == 5001


def test_add_day_ticket_rejects_unknown_ticket(monkeypatch) -> None:
    monkeypatch.setattr(web_app, "load_config", lambda: _fake_config())
    monkeypatch.setattr(web_app, "JiraClient", _FakeJiraClientNoLogs)

    client = TestClient(web_app.app)
    resp = client.post(
        "/api/day/tickets",
        json={"date": "2026-04-02", "issue_key": "PROJ-999"},
    )

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_month_plan_skips_status_queries_for_fully_logged_day(monkeypatch) -> None:
    _FakeJiraClientMonthPrefetch.reset()
    monkeypatch.setattr(web_app, "load_config", lambda: _fake_config())
    monkeypatch.setattr(web_app, "JiraClient", _FakeJiraClientMonthPrefetch)
    monkeypatch.setattr(web_app, "suggest_minutes_from_git", lambda repo_path, since, until: {})
    monkeypatch.setattr(web_app, "_parse_month_param", lambda month, tzinfo: (2026, 4))

    client = TestClient(web_app.app)
    resp = client.get("/api/month/plan?month=2026-04")

    assert resp.status_code == 200
    payload = resp.json()
    day = next(item for item in payload["days"] if item["date"] == "2026-04-01")
    assert day["total_minutes"] >= 480
    assert day["entries"][0]["source"] == "logged"
    assert all('ON "2026-04-01"' not in jql for jql in _FakeJiraClientMonthPrefetch.search_issue_jql_calls)


def test_month_prefetch_logs_warning_when_more_than_first_page(caplog, monkeypatch) -> None:
    _FakeJiraClientMonthPrefetchTruncated.reset()
    monkeypatch.setattr(web_app, "load_config", lambda: _fake_config())
    monkeypatch.setattr(web_app, "JiraClient", _FakeJiraClientMonthPrefetchTruncated)
    monkeypatch.setattr(web_app, "suggest_minutes_from_git", lambda repo_path, since, until: {})
    monkeypatch.setattr(web_app, "_parse_month_param", lambda month, tzinfo: (2026, 4))

    caplog.set_level(logging.WARNING)
    client = TestClient(web_app.app)
    resp = client.get("/api/month/plan?month=2026-04")

    assert resp.status_code == 200
    assert "month_worklog_prefetch_truncated" in caplog.text


def test_refresh_day_forces_secondary_query_even_with_8h_logged(monkeypatch) -> None:
    _FakeJiraClientRefreshForce.reset()
    monkeypatch.setattr(web_app, "load_config", lambda: _fake_config_with_secondary())
    monkeypatch.setattr(web_app, "JiraClient", _FakeJiraClientRefreshForce)

    client = TestClient(web_app.app)
    resp = client.post("/api/day/refresh", json={"date": "2026-04-02"})

    assert resp.status_code == 200
    payload = resp.json()
    keys = {entry["issue_key"] for entry in payload["entries"]}
    assert "PROJ-100" in keys
    assert "PROJ-200" in keys
    assert any("In Review" in jql for jql in _FakeJiraClientRefreshForce.search_issue_jql_calls)
