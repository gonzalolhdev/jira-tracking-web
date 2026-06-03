from __future__ import annotations

from pathlib import Path

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
        _ = (jql, max_results)
        return [
            Issue(key="PROJ-100", issue_type="Bug", summary="Bug issue", status="In Progress"),
            Issue(key="PROJ-101", issue_type="Story", summary="Story issue", status="In Progress"),
        ]

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
