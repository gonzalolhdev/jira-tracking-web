import json
import os
from pathlib import Path

import pytest

from jira_tracking_bot.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def clear_jira_track_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "JIRA_TRACK_JIRA_BASE_URL",
        "JIRA_TRACK_JIRA_EMAIL",
        "JIRA_TRACK_JIRA_TOKEN",
        "JIRA_TRACK_TEMPO_API_TOKEN",
        "JIRA_TRACK_TEMPO_API_BASE",
        "JIRA_TRACK_TIMEZONE",
        "JIRA_TRACK_DEFAULT_PROJECTS",
        "JIRA_TRACK_IN_PROGRESS_STATUSES",
        "JIRA_TRACK_DAILY_SNAPSHOT_MODE",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)



def test_loads_project_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    env_content = "\n".join(
        [
            "JIRA_TRACK_JIRA_BASE_URL=https://example.atlassian.net",
            "JIRA_TRACK_JIRA_EMAIL=user@example.com",
            "JIRA_TRACK_JIRA_TOKEN=token-123",
            "JIRA_TRACK_TIMEZONE=America/Argentina/Buenos_Aires",
            "JIRA_TRACK_DEFAULT_PROJECTS=PROJ,OPS,PLAT",
        ]
    )
    (tmp_path / ".env").write_text(env_content)

    config = load_config(require_auth=False)

    assert config.jira_base_url == "https://example.atlassian.net"
    assert config.jira_email == "user@example.com"
    assert config.jira_token == "token-123"
    assert config.timezone == "America/Argentina/Buenos_Aires"
    assert config.default_projects == ["PROJ", "OPS", "PLAT"]



def test_prefers_project_json_before_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project_config = {
        "JIRA_BASE_URL": "https://project.atlassian.net",
        "JIRA_EMAIL": "project@example.com",
        "JIRA_TOKEN": "project-token",
        "TIMEZONE": "UTC",
        "DEFAULT_PROJECTS": ["AAA", "BBB"],
    }
    home_config = {
        "JIRA_BASE_URL": "https://home.atlassian.net",
        "JIRA_EMAIL": "home@example.com",
        "JIRA_TOKEN": "home-token",
        "TIMEZONE": "UTC",
        "DEFAULT_PROJECTS": ["ZZZ"],
    }

    (tmp_path / ".jira-track.json").write_text(json.dumps(project_config))
    home_path = tmp_path / "home"
    home_path.mkdir()
    (home_path / ".jira-track.json").write_text(json.dumps(home_config))
    monkeypatch.setenv("HOME", str(home_path))

    config = load_config(require_auth=False)

    assert config.jira_base_url == "https://project.atlassian.net"
    assert config.jira_email == "project@example.com"
    assert config.jira_token == "project-token"
    assert config.default_projects == ["AAA", "BBB"]



def test_existing_env_vars_are_not_overridden_by_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "JIRA_TRACK_TIMEZONE=America/Argentina/Buenos_Aires",
                "JIRA_TRACK_JIRA_EMAIL=from-env-file@example.com",
                "JIRA_TRACK_JIRA_TOKEN=from-env-file-token",
            ]
        )
        + "\n"
    )
    monkeypatch.setenv("JIRA_TRACK_JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_TRACK_JIRA_EMAIL", "from-real-env@example.com")
    monkeypatch.setenv("JIRA_TRACK_JIRA_TOKEN", "from-real-env-token")
    monkeypatch.setenv("JIRA_TRACK_TIMEZONE", "UTC")

    config = load_config()

    assert config.timezone == "UTC"
    assert config.jira_email == "from-real-env@example.com"
    assert config.jira_token == "from-real-env-token"
    assert os.environ["JIRA_TRACK_TIMEZONE"] == "UTC"


def test_base_url_defaults_to_https_when_scheme_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    env_content = "\n".join(
        [
            "JIRA_TRACK_JIRA_BASE_URL=jira.example.com",
            "JIRA_TRACK_JIRA_EMAIL=user@example.com",
            "JIRA_TRACK_JIRA_TOKEN=token-123",
        ]
    )
    (tmp_path / ".env").write_text(env_content)

    config = load_config()

    assert config.jira_base_url == "https://jira.example.com"


def test_timezone_defaults_to_new_york_when_not_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "JIRA_TRACK_JIRA_BASE_URL=https://jira.example.com",
                "JIRA_TRACK_JIRA_EMAIL=user@example.com",
                "JIRA_TRACK_JIRA_TOKEN=token-123",
            ]
        )
    )

    config = load_config()

    assert config.timezone == "America/New_York"


def test_load_config_requires_jira_credentials_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("JIRA_TRACK_JIRA_BASE_URL=https://jira.example.com\n")

    with pytest.raises(ConfigError) as exc:
        load_config()

    assert "JIRA_EMAIL" in str(exc.value)
    assert "JIRA_TOKEN" in str(exc.value)


def test_load_config_can_skip_auth_validation_for_health_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("JIRA_TRACK_JIRA_BASE_URL=https://jira.example.com\n")

    config = load_config(require_auth=False)

    assert config.resolved_auth_mode == "api-token"
    assert config.has_jira_credentials is False


def test_daily_snapshot_mode_defaults_to_on_day_when_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "JIRA_TRACK_JIRA_BASE_URL=https://jira.example.com",
                "JIRA_TRACK_JIRA_EMAIL=user@example.com",
                "JIRA_TRACK_JIRA_TOKEN=token-123",
            ]
        )
    )

    config = load_config()

    assert config.daily_snapshot_mode == "on_day"


def test_daily_snapshot_mode_reads_on_day_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "JIRA_TRACK_JIRA_BASE_URL=https://jira.example.com",
                "JIRA_TRACK_JIRA_EMAIL=user@example.com",
                "JIRA_TRACK_JIRA_TOKEN=token-123",
                "JIRA_TRACK_DAILY_SNAPSHOT_MODE=on_day",
            ]
        )
    )

    config = load_config()

    assert config.daily_snapshot_mode == "on_day"


def test_daily_snapshot_mode_reads_bounded_during_from_config_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".jira-track.json").write_text(
        json.dumps(
            {
                "JIRA_BASE_URL": "https://jira.example.com",
                "JIRA_EMAIL": "user@example.com",
                "JIRA_TOKEN": "token-123",
                "DAILY_SNAPSHOT_MODE": "bounded_during",
            }
        )
    )

    config = load_config()

    assert config.daily_snapshot_mode == "bounded_during"


def test_daily_snapshot_mode_invalid_value_falls_back_to_on_day(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "JIRA_TRACK_JIRA_BASE_URL=https://jira.example.com",
                "JIRA_TRACK_JIRA_EMAIL=user@example.com",
                "JIRA_TRACK_JIRA_TOKEN=token-123",
                "JIRA_TRACK_DAILY_SNAPSHOT_MODE=bad-mode",
            ]
        )
    )

    config = load_config()

    assert config.daily_snapshot_mode == "on_day"
