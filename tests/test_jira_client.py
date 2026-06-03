from pathlib import Path

import pytest

from jira_tracking_bot.config import AppConfig
from jira_tracking_bot.jira_client import JiraClient, JiraClientError


def test_sso_request_missing_session_raises_jira_client_error(tmp_path: Path) -> None:
    config = AppConfig(
        jira_base_url="https://jira.example.com",
        timezone="UTC",
        session_state_path=(tmp_path / ".jira-track" / "missing-session.json").resolve(),
    )

    client = JiraClient(config)
    try:
        with pytest.raises(JiraClientError) as exc:
            client.get_issue_worklogs("ABC-123")
    finally:
        client.close()

    assert "SSO session state not found" in str(exc.value)
