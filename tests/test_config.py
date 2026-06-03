import json
import os
from pathlib import Path

import pytest

from jira_tracking_bot.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def clear_jira_track_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "JIRA_TRACK_JIRA_BASE_URL",
        "JIRA_TRACK_TEMPO_API_TOKEN",
        "JIRA_TRACK_TEMPO_API_BASE",
        "JIRA_TRACK_TIMEZONE",
        "JIRA_TRACK_DEFAULT_PROJECTS",
        "JIRA_TRACK_IN_PROGRESS_STATUSES",
        "JIRA_TRACK_SESSION_STATE_PATH",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)



def test_loads_project_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    env_content = "\n".join(
        [
            "JIRA_TRACK_JIRA_BASE_URL=https://example.atlassian.net",
            "JIRA_TRACK_TIMEZONE=America/Argentina/Buenos_Aires",
            "JIRA_TRACK_DEFAULT_PROJECTS=PROJ,OPS,PLAT",
        ]
    )
    (tmp_path / ".env").write_text(env_content)

    session_dir = tmp_path / ".jira-track"
    session_dir.mkdir()
    (session_dir / "session.json").write_text('{"cookies": [], "origins": []}')

    config = load_config(require_auth=False)

    assert config.jira_base_url == "https://example.atlassian.net"
    assert config.timezone == "America/Argentina/Buenos_Aires"
    assert config.default_projects == ["PROJ", "OPS", "PLAT"]



def test_prefers_project_json_before_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project_config = {
        "JIRA_BASE_URL": "https://project.atlassian.net",
        "TIMEZONE": "UTC",
        "DEFAULT_PROJECTS": ["AAA", "BBB"],
    }
    home_config = {
        "JIRA_BASE_URL": "https://home.atlassian.net",
        "TIMEZONE": "UTC",
        "DEFAULT_PROJECTS": ["ZZZ"],
    }

    (tmp_path / ".jira-track.json").write_text(json.dumps(project_config))
    home_path = tmp_path / "home"
    home_path.mkdir()
    (home_path / ".jira-track.json").write_text(json.dumps(home_config))
    monkeypatch.setenv("HOME", str(home_path))
    session_dir = tmp_path / ".jira-track"
    session_dir.mkdir()
    (session_dir / "session.json").write_text('{"cookies": [], "origins": []}')

    config = load_config(require_auth=False)

    assert config.jira_base_url == "https://project.atlassian.net"
    assert config.default_projects == ["AAA", "BBB"]



def test_existing_env_vars_are_not_overridden_by_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("JIRA_TRACK_TIMEZONE=America/Argentina/Buenos_Aires\n")
    monkeypatch.setenv("JIRA_TRACK_JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_TRACK_TIMEZONE", "UTC")
    session_dir = tmp_path / ".jira-track"
    session_dir.mkdir()
    (session_dir / "session.json").write_text('{"cookies": [], "origins": []}')

    config = load_config()

    assert config.timezone == "UTC"
    assert os.environ["JIRA_TRACK_TIMEZONE"] == "UTC"


def test_base_url_defaults_to_https_when_scheme_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    env_content = "\n".join(
        [
            "JIRA_TRACK_JIRA_BASE_URL=jira.example.com",
        ]
    )
    (tmp_path / ".env").write_text(env_content)
    session_dir = tmp_path / ".jira-track"
    session_dir.mkdir()
    (session_dir / "session.json").write_text('{"cookies": [], "origins": []}')

    config = load_config()

    assert config.jira_base_url == "https://jira.example.com"


def test_timezone_defaults_to_new_york_when_not_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("JIRA_TRACK_JIRA_BASE_URL=https://jira.example.com\n")
    session_dir = tmp_path / ".jira-track"
    session_dir.mkdir()
    (session_dir / "session.json").write_text('{"cookies": [], "origins": []}')

    config = load_config()

    assert config.timezone == "America/New_York"


def test_load_config_requires_sso_session_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("JIRA_TRACK_JIRA_BASE_URL=https://jira.example.com\n")

    with pytest.raises(ConfigError) as exc:
        load_config()

    assert "SESSION_STATE_PATH" in str(exc.value)
    assert "login-sso" in str(exc.value)


def test_load_config_can_skip_auth_validation_for_login_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("JIRA_TRACK_JIRA_BASE_URL=https://jira.example.com\n")

    config = load_config(require_auth=False)

    assert config.resolved_auth_mode == "sso"
    assert config.has_sso_session is False


def test_session_state_path_is_resolved_to_absolute_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "JIRA_TRACK_JIRA_BASE_URL=https://jira.example.com",
                "JIRA_TRACK_SESSION_STATE_PATH=.jira-track/session.json",
            ]
        )
    )

    config = load_config(require_auth=False)

    assert config.session_state_path == (tmp_path / ".jira-track" / "session.json").resolve()
