import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from jira_tracking_bot.config import AppConfig
from jira_tracking_bot.jira_client import JiraClient, JiraClientError
from jira_tracking_bot.models import Issue, WorklogEntry


def test_request_missing_token_credentials_raises_jira_client_error() -> None:
    config = AppConfig(
        jira_base_url="https://jira.example.com",
        jira_email="",
        jira_token="",
        timezone="UTC",
    )

    client = JiraClient(config)
    try:
        with pytest.raises(JiraClientError) as exc:
            client.get_issue_worklogs("ABC-123")
    finally:
        client.close()

    assert "Jira credentials are not configured" in str(exc.value)


def test_create_worklog_uses_cloud_compatible_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AppConfig(
        jira_base_url="https://example.atlassian.net",
        jira_email="user@example.com",
        jira_token="token",
        timezone="UTC",
    )
    client = JiraClient(config)
    captured: dict[str, object] | None = None

    class DummyTransport:
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal captured
            captured = json.loads(request.content.decode()) if request.content else None
            return httpx.Response(201, request=request, json={"id": "10001"})

    class DummyClient:
        def __init__(self) -> None:
            self._transport = DummyTransport()

        def request(self, method: str, url: str, *, json: dict | None = None, **kwargs: object) -> httpx.Response:
            request = httpx.Request(method, url, json=json)
            return self._transport.handle_request(request)

        def close(self) -> None:
            pass

    monkeypatch.setattr(client, "_jira_client", DummyClient())
    issue = Issue(key="MWW-10195", issue_type="Task", summary="Fix issue", status="In Progress")
    entry = WorklogEntry(
        issue=issue,
        time_minutes=60,
        comment=None,
        started_at=datetime(2026, 8, 28, 9, 0, tzinfo=ZoneInfo("UTC")),
    )

    try:
        client.create_worklog(entry)
    finally:
        client.close()

    assert captured is not None
    assert captured["started"] == "2026-08-28T09:00:00.000+0000"
    assert captured["timeSpentSeconds"] == 3600
    assert captured["comment"]["type"] == "doc"
    assert captured["comment"]["content"][0]["content"][0]["text"] == "Working on issue MWW-10195"
