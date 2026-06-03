import json
from pathlib import Path

import pytest

from jira_tracking_bot.session_auth import SessionAuthError, load_session_cookies, session_summary



def test_load_session_cookies_reads_playwright_storage_state(tmp_path: Path) -> None:
    session_path = tmp_path / "session.json"
    session_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "JSESSIONID",
                        "value": "abc123",
                        "domain": "jira.example.com",
                        "path": "/",
                    }
                ],
                "origins": [],
            }
        )
    )

    cookies = load_session_cookies(session_path)

    cookie_header = cookies.get("JSESSIONID", domain="jira.example.com", path="/")
    assert cookie_header == "abc123"



def test_load_session_cookies_requires_existing_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.json"

    with pytest.raises(SessionAuthError):
        load_session_cookies(missing_path)



def test_session_summary_reports_counts(tmp_path: Path) -> None:
    session_path = tmp_path / "session.json"
    session_path.write_text(
        json.dumps(
            {
                "cookies": [{"name": "a", "value": "1", "domain": "jira.example.com", "path": "/"}],
                "origins": [{"origin": "https://jira.example.com", "localStorage": []}],
            }
        )
    )

    summary = session_summary(session_path)

    assert summary == {"exists": True, "cookies": 1, "origins": 1}
