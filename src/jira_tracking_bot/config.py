from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


CONFIG_ENV_PREFIX = "JIRA_TRACK_"
DEFAULT_CONFIG_PATH = Path.home() / ".jira-track.json"
PROJECT_CONFIG_PATH = Path(".jira-track.json")
PROJECT_ENV_PATH = Path(".env")
PRODUCTIVE_ENV_PATH = Path(".env.productive")
DEFAULT_SESSION_STATE_PATH = Path(".jira-track/session.json")


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


@dataclass(slots=True)
class AppConfig:
    jira_base_url: str
    timezone: str
    tempo_api_token: str | None = None
    tempo_api_base: str = "https://api.tempo.io/4"
    default_projects: list[str] | None = None
    in_progress_statuses: list[str] | None = None
    secondary_tracking_statuses: list[str] | None = None
    session_state_path: Path = DEFAULT_SESSION_STATE_PATH
    debug: bool = False

    @property
    def has_sso_session(self) -> bool:
        return self.session_state_path.exists()

    @property
    def resolved_auth_mode(self) -> str:
        return "sso"

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception as exc:  # pragma: no cover
            raise ConfigError(f"Invalid timezone: {self.timezone}") from exc



@dataclass(slots=True)
class ProductiveConfig:
    base_url: str
    token: str
    org_id: str


def load_productive_config() -> ProductiveConfig | None:
    """Load Productive API config from .env.productive or environment variables.

    Returns ``None`` when the required variables are not present.
    """
    _load_env_file(Path.cwd() / PRODUCTIVE_ENV_PATH)

    base_url = os.getenv("PRODUCTIVE_BASE_URL", "https://api.productive.io/api/v2")
    token = os.getenv("PRODUCTIVE_TOKEN")
    org_id = os.getenv("PRODUCTIVE_ORG_ID")

    if not token or not org_id:
        return None

    return ProductiveConfig(base_url=base_url, token=token, org_id=org_id)


def load_config(config_path: str | None = None, require_auth: bool = True) -> AppConfig:
    _load_project_env_file()
    path = _resolve_config_path(config_path)
    file_values = _load_json(path)

    jira_base_url = _read_value("JIRA_BASE_URL", file_values)
    normalized_base_url = _normalize_base_url(jira_base_url) if jira_base_url else None
    tempo_api_token = _read_value("TEMPO_API_TOKEN", file_values)
    tempo_api_base = _read_value("TEMPO_API_BASE", file_values, default="https://api.tempo.io/4")
    timezone = _read_value("TIMEZONE", file_values, default="America/New_York")
    default_projects = _read_projects(file_values)
    in_progress_statuses = _read_in_progress_statuses(file_values)
    secondary_tracking_statuses = _read_secondary_tracking_statuses(file_values)
    session_state_path = _read_session_state_path(file_values)
    debug = _read_value("DEBUG", file_values, default="false").lower() in ("true", "1", "yes")

    config = AppConfig(
        jira_base_url=normalized_base_url,
        tempo_api_token=tempo_api_token,
        timezone=timezone,
        tempo_api_base=tempo_api_base or "https://api.tempo.io/4",
        default_projects=default_projects,
        in_progress_statuses=in_progress_statuses,
        secondary_tracking_statuses=secondary_tracking_statuses,
        session_state_path=session_state_path,
        debug=debug,
    )

    missing = [name for name, value in {"JIRA_BASE_URL": normalized_base_url}.items() if not value]
    if require_auth and not config.has_sso_session:
        missing.append(f"SESSION_STATE_PATH ({config.session_state_path})")
    if missing:
        missing_joined = ", ".join(missing)
        raise ConfigError(
            f"Missing config keys: {missing_joined}. "
            f"Set env vars with prefix {CONFIG_ENV_PREFIX}, add them to {PROJECT_ENV_PATH}, or add them to {path}. "
            f"For SSO mode, run `jira-track login-sso` to create a session file."
        )

    return config



def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file {path} must contain an object")

    return {str(k): v for k, v in raw.items() if v is not None}


def _resolve_config_path(config_path: str | None) -> Path:
    if config_path:
        return Path(config_path)

    project_path = Path.cwd() / PROJECT_CONFIG_PATH
    if project_path.exists():
        return project_path

    return DEFAULT_CONFIG_PATH


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _load_project_env_file() -> None:
    _load_env_file(Path.cwd() / PROJECT_ENV_PATH)



def _read_value(key: str, file_values: dict[str, Any], default: str | None = None) -> str | None:
    env_key = f"{CONFIG_ENV_PREFIX}{key}"
    env_value = os.getenv(env_key)
    if env_value is not None:
        return env_value
    file_value = file_values.get(key, default)
    if file_value is None:
        return default
    return str(file_value)


def _read_projects(file_values: dict[str, Any]) -> list[str] | None:
    env_key = f"{CONFIG_ENV_PREFIX}DEFAULT_PROJECTS"
    env_value = os.getenv(env_key)
    if env_value is not None:
        return _normalize_projects(env_value.split(","))

    projects_value = file_values.get("DEFAULT_PROJECTS")
    if projects_value is not None:
        if isinstance(projects_value, list):
            return _normalize_projects(projects_value)
        if isinstance(projects_value, str):
            return _normalize_projects(projects_value.split(","))

    legacy_value = file_values.get("DEFAULT_PROJECT")
    if legacy_value is not None:
        return _normalize_projects([legacy_value])

    return None


def _read_in_progress_statuses(file_values: dict[str, Any]) -> list[str] | None:
    env_key = f"{CONFIG_ENV_PREFIX}IN_PROGRESS_STATUSES"
    env_value = os.getenv(env_key)
    if env_value is not None:
        return _normalize_projects(env_value.split(","))

    statuses_value = file_values.get("IN_PROGRESS_STATUSES")
    if statuses_value is not None:
        if isinstance(statuses_value, list):
            return _normalize_projects(statuses_value)
        if isinstance(statuses_value, str):
            return _normalize_projects(statuses_value.split(","))

    return ["In Progress"]


def _read_secondary_tracking_statuses(file_values: dict[str, Any]) -> list[str] | None:
    env_key = f"{CONFIG_ENV_PREFIX}SECONDARY_TRACKING_STATUSES"
    env_value = os.getenv(env_key)
    if env_value is not None:
        return _normalize_projects(env_value.split(","))

    statuses_value = file_values.get("SECONDARY_TRACKING_STATUSES")
    if statuses_value is not None:
        if isinstance(statuses_value, list):
            return _normalize_projects(statuses_value)
        if isinstance(statuses_value, str):
            return _normalize_projects(statuses_value.split(","))

    return None


def _normalize_projects(values: list[Any]) -> list[str] | None:
    normalized = [str(value).strip() for value in values if str(value).strip()]
    return normalized or None


def _read_session_state_path(file_values: dict[str, Any]) -> Path:
    value = _read_value("SESSION_STATE_PATH", file_values, default=str(DEFAULT_SESSION_STATE_PATH))
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def _normalize_base_url(value: str) -> str:
    stripped = value.strip().rstrip("/")
    parsed = urlparse(stripped)
    if parsed.scheme:
        return stripped
    return f"https://{stripped}"
