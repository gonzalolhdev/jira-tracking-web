from __future__ import annotations

from fastapi.testclient import TestClient

from jira_tracking_bot.config import AppConfig
from jira_tracking_bot.jira_client import JiraClientError
from jira_tracking_bot.web import app as web_app


class _FakeJiraClientOk:
    def __init__(self, _config: AppConfig) -> None:
        pass

    def validate_auth(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeJiraClientFail(_FakeJiraClientOk):
    def validate_auth(self) -> None:
        raise JiraClientError("HTTP 401. Unauthorized")


def test_auth_status_reports_token_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app,
        "load_config",
        lambda require_auth=False: AppConfig(
            jira_base_url="https://jira.example.com",
            jira_email="",
            jira_token="",
            timezone="UTC",
        ),
    )

    client = TestClient(web_app.app)
    resp = client.get("/api/auth/status")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["auth_mode"] == "api-token"
    assert payload["token_configured"] is False
    assert payload["token_valid"] is False
    assert "JIRA_TRACK_JIRA_EMAIL" in payload["validation_error"]


def test_auth_status_reports_invalid_token(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app,
        "load_config",
        lambda require_auth=False: AppConfig(
            jira_base_url="https://jira.example.com",
            jira_email="user@example.com",
            jira_token="bad-token",
            timezone="UTC",
        ),
    )
    monkeypatch.setattr(web_app, "JiraClient", _FakeJiraClientFail)

    client = TestClient(web_app.app)
    resp = client.get("/api/auth/status")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["token_configured"] is True
    assert payload["token_valid"] is False
    assert "401" in payload["validation_error"]


def test_auth_status_reports_valid_token(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app,
        "load_config",
        lambda require_auth=False: AppConfig(
            jira_base_url="https://jira.example.com",
            jira_email="user@example.com",
            jira_token="good-token",
            timezone="UTC",
        ),
    )
    monkeypatch.setattr(web_app, "JiraClient", _FakeJiraClientOk)

    client = TestClient(web_app.app)
    resp = client.get("/api/auth/status")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["token_configured"] is True
    assert payload["token_valid"] is True
    assert payload["validation_error"] is None
