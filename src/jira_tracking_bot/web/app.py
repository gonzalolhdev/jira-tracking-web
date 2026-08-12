from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncGenerator
from datetime import date, datetime, time, timedelta
import json
import logging
import os
from pathlib import Path
import re
import time as _time
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from jira_tracking_bot.balancing import DAILY_TARGET_MINUTES, rebalance_day, rebalance_month
from jira_tracking_bot.config import ConfigError, load_config
from jira_tracking_bot.discovery import (
    build_jql_for_day_secondary_snapshot,
    build_jql_for_day_snapshot,
    build_jql_for_month_worklog_tickets,
)
from jira_tracking_bot.heuristics.git_signals import suggest_minutes_from_git
from jira_tracking_bot.jira_client import JiraClient, JiraClientError
from jira_tracking_bot.models import Issue, WorklogEntry
from jira_tracking_bot.session_auth import SessionAuthError
from jira_tracking_bot.session_auth import delete_browser_session, login_with_browser_session_web, session_summary
from jira_tracking_bot.web.schemas import (
    DayAddTicketRequest,
    DayPlan,
    DayPlanEntry,
    DayRefreshRequest,
    MonthPlanResponse,
    RebalanceRequest,
    SubmitItemResult,
    SubmitRequest,
    SubmitResponse,
)


logger = logging.getLogger(__name__)

# Configure logging based on DEBUG flag
def _configure_logging() -> None:
    debug = False
    try:
        config = load_config(require_auth=False)
        debug = config.debug
    except ConfigError:
        pass
    
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,  # Override any existing config
    )
    logging.getLogger("jira_tracking_bot").setLevel(log_level)

_configure_logging()

SEARCH_CONCURRENCY = 5
WORKLOG_CONCURRENCY = 10
WORKLOG_PREFETCH_PAGE_SIZE = 100
ISSUE_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-[0-9]+$")


def _env_truthy(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _cors_origins() -> list[str]:
    raw = os.getenv("JIRA_TRACK_CORS_ORIGINS")
    if raw is None:
        return ["http://localhost:5173", "http://127.0.0.1:5173"]
    parsed = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return parsed or ["http://localhost:5173", "http://127.0.0.1:5173"]


def _cdp_allowed_hosts() -> set[str]:
    raw = os.getenv("JIRA_TRACK_CDP_ALLOWED_HOSTS")
    if raw is None:
        return {"127.0.0.1", "localhost", "host.docker.internal"}
    parsed = {host.strip().lower() for host in raw.split(",") if host.strip()}
    return parsed or {"127.0.0.1", "localhost", "host.docker.internal"}


def _validate_cdp_url(cdp_url: str) -> str:
    parsed = urlparse(cdp_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SessionAuthError("Invalid CDP URL. Expected format like http://127.0.0.1:9222")

    allowed_hosts = _cdp_allowed_hosts()
    if parsed.hostname.lower() not in allowed_hosts:
        allowed = ", ".join(sorted(allowed_hosts))
        raise SessionAuthError(
            f"CDP URL host '{parsed.hostname}' is not allowed. Allowed hosts: {allowed}"
        )

    return cdp_url


def _parse_month_param(month: str | None, tzinfo: object) -> tuple[int, int]:
    """Parse and validate the optional ``month`` query parameter.

    Allowed values: current month and the two immediately preceding months
    (evaluated in the server timezone).  Returns ``(year, month_number)``.
    Raises ``HTTPException(400)`` when the value is outside the allowed window
    or is not parseable.
    """
    from zoneinfo import ZoneInfo
    import datetime as _dt

    now = _dt.datetime.now(tzinfo)  # type: ignore[arg-type]

    if month is None:
        return now.year, now.month

    try:
        parts = month.split("-")
        if len(parts) != 2:
            raise ValueError("bad format")
        req_year, req_month = int(parts[0]), int(parts[1])
        if not (1 <= req_month <= 12):
            raise ValueError("month out of range")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid month format '{month}'. Expected YYYY-MM.")

    # Build set of allowed (year, month) pairs: current + previous 2
    allowed: set[tuple[int, int]] = set()
    y, m = now.year, now.month
    for _ in range(3):
        allowed.add((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    if (req_year, req_month) not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Month '{month}' is outside the allowed window (current month and two previous months).",
        )

    return req_year, req_month

app = FastAPI(title="Jira Tracking Bot Web API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/auth/status")
def auth_status() -> dict[str, object]:
    try:
        config = load_config(require_auth=False)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    summary = session_summary(config.session_state_path)
    return {
        "auth_mode": "sso",
        "session_path": str(config.session_state_path),
        "session_exists": bool(summary.get("exists")),
        "cookies": int(summary.get("cookies", 0)),
        "jira_base_url": config.jira_base_url,
    }


class LoginSsoRequest(BaseModel):
    cdp_url: str | None = None


@app.post("/api/auth/login-sso")
def login_sso_web(payload: LoginSsoRequest | None = None) -> dict[str, str]:
    try:
        config = load_config(require_auth=False)
        cdp_url = (payload.cdp_url if payload else None) or os.getenv("JIRA_TRACK_CDP_URL")
        if cdp_url:
            cdp_url = _validate_cdp_url(cdp_url)
        session_path = login_with_browser_session_web(config, cdp_url=cdp_url)
    except (ConfigError, SessionAuthError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"message": "SSO session saved", "session_path": str(session_path)}


@app.post("/api/auth/logout")
def logout_web() -> dict[str, str]:
    try:
        config = load_config(require_auth=False)
        delete_browser_session(config)
    except (ConfigError, SessionAuthError):
        pass
    return {"message": "Logged out"}


async def _search_day_async(
    client: JiraClient,
    config: object,
    day: date,
    semaphore: asyncio.Semaphore,
) -> tuple[date, list[Issue], str | None]:
    """Fetch issues for a single weekday (thread-wrapped for asyncio)."""
    async with semaphore:
        jql = build_jql_for_day_snapshot(config, day)  # type: ignore[arg-type]
        try:
            issues = await asyncio.to_thread(client.search_issues, jql=jql)
            return day, _dedupe_issues_by_key(issues), None
        except JiraClientError as exc:
            return day, [], str(exc)


async def _search_day_secondary_async(
    client: JiraClient,
    config: object,
    day: date,
    semaphore: asyncio.Semaphore,
) -> tuple[date, list[Issue], str | None]:
    """Fetch secondary-status issues for a single weekday.

    Returns an empty list (no error) when no secondary statuses are configured
    so the caller does not need to special-case the absence of the feature.
    """
    async with semaphore:
        jql = build_jql_for_day_secondary_snapshot(config, day)  # type: ignore[arg-type]
        if jql is None:
            return day, [], None
        try:
            issues = await asyncio.to_thread(client.search_issues, jql=jql)
            return day, _dedupe_issues_by_key(issues), None
        except JiraClientError as exc:
            return day, [], str(exc)



async def _fetch_issue_worklogs_async(
    client: JiraClient,
    issue_key: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, list[dict]]:
    """Fetch Jira worklogs for a single issue (thread-wrapped for asyncio)."""
    async with semaphore:
        try:
            worklogs = await asyncio.to_thread(client.get_issue_worklogs, issue_key)
            return issue_key, worklogs
        except JiraClientError:
            return issue_key, []


def _build_logged_day_plan_from_existing(
    day: date,
    existing: dict[tuple[str, date], tuple[int, int | None]],
    issue_lookup: dict[str, Issue],
) -> DayPlan:
    day_items = [
        (issue_key, minutes, worklog_id)
        for (issue_key, issue_day), (minutes, worklog_id) in existing.items()
        if issue_day == day and minutes > 0
    ]
    day_items.sort(key=lambda item: item[0])

    entries: list[DayPlanEntry] = []
    for issue_key, minutes, worklog_id in day_items:
        issue = issue_lookup.get(issue_key)
        entries.append(
            DayPlanEntry(
                date=day,
                issue_key=issue_key,
                issue_type=issue.issue_type if issue else "Unknown",
                summary=issue.summary if issue else "",
                status=issue.status if issue else "Unknown",
                minutes=minutes,
                source="logged",
                locked=False,
                removed=False,
                worklog_id=worklog_id,
            )
        )

    return DayPlan(date=day, entries=entries, total_minutes=sum(e.minutes for e in entries))


async def _prefetch_month_logged_context(
    client: JiraClient,
    config: object,
    weekdays: list[date],
) -> tuple[
    str | None,
    list[dict],
    dict[str, Issue],
    dict[str, list[dict]],
    dict[tuple[str, date], tuple[int, int | None]],
    set[date],
]:
    """Prefetch month worklog issues and derive fully-logged weekdays.

    Returns:
    - current_user_id
    - tempo_worklogs
    - prefetched_issues_by_key
    - prefetched_issue_worklogs_by_key
    - existing_prefetched map (issue, day) -> (minutes, worklog_id)
    - fully_logged_days (sum logged minutes >= 8h)
    """
    if not weekdays:
        return None, [], {}, {}, {}, set()

    current_user_id: str | None = None
    try:
        current_user_id = await asyncio.to_thread(client.get_current_user_account_id)
    except JiraClientError:
        pass

    if not current_user_id:
        return None, [], {}, {}, {}, set()

    tempo_worklogs: list[dict] = []
    try:
        _uid = current_user_id
        tempo_worklogs = await asyncio.to_thread(
            lambda: client.get_tempo_worklogs_for_user(
                account_id=_uid,
                from_date=weekdays[0],
                to_date=weekdays[-1],
            )
        )
    except JiraClientError:
        pass

    prefetched_issues: list[Issue] = []
    prefetched_total = 0
    month_worklog_jql = build_jql_for_month_worklog_tickets(config, weekdays[0], weekdays[-1])  # type: ignore[arg-type]
    try:
        prefetched_issues, prefetched_total = await asyncio.to_thread(
            client.search_issues_page,
            jql=month_worklog_jql,
            max_results=WORKLOG_PREFETCH_PAGE_SIZE,
            start_at=0,
        )
    except JiraClientError:
        prefetched_issues = []

    prefetched_issues_by_key = {issue.key: issue for issue in _dedupe_issues_by_key(prefetched_issues)}
    if prefetched_total > len(prefetched_issues_by_key):
        logger.warning(
            "month_worklog_prefetch_truncated fetched=%s total=%s page_size=%s",
            len(prefetched_issues_by_key),
            prefetched_total,
            WORKLOG_PREFETCH_PAGE_SIZE,
        )

    prefetched_issue_worklogs_by_key: dict[str, list[dict]] = {}
    if prefetched_issues_by_key:
        wl_semaphore = asyncio.Semaphore(WORKLOG_CONCURRENCY)
        wl_tasks = [
            _fetch_issue_worklogs_async(client, issue_key, wl_semaphore)
            for issue_key in prefetched_issues_by_key
        ]
        wl_results = await asyncio.gather(*wl_tasks)
        prefetched_issue_worklogs_by_key = dict(wl_results)

    existing_prefetched: dict[tuple[str, date], tuple[int, int | None]] = {}
    if prefetched_issues_by_key:
        existing_prefetched = _build_existing_map_from_raw(
            current_user_id,
            prefetched_issue_worklogs_by_key,
            tempo_worklogs,
            weekdays[0],
            weekdays[-1],
            set(prefetched_issues_by_key.keys()),
        )

    logged_minutes_by_day: dict[date, int] = defaultdict(int)
    for (_issue_key, issue_day), (minutes, _worklog_id) in existing_prefetched.items():
        logged_minutes_by_day[issue_day] += minutes

    fully_logged_days = {
        day
        for day, logged_minutes in logged_minutes_by_day.items()
        if logged_minutes >= DAILY_TARGET_MINUTES
    }

    logger.info(
        "month_worklog_prefetch_done issues=%s fully_logged_days=%s",
        len(prefetched_issues_by_key),
        len(fully_logged_days),
    )

    return (
        current_user_id,
        tempo_worklogs,
        prefetched_issues_by_key,
        prefetched_issue_worklogs_by_key,
        existing_prefetched,
        fully_logged_days,
    )


@app.get("/api/month/plan", response_model=MonthPlanResponse)
async def get_month_plan(repo_path: str = ".", month: str | None = None) -> MonthPlanResponse:
    t0 = _time.monotonic()
    try:
        config = load_config()
        client = JiraClient(config)
    except (ConfigError, SessionAuthError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    req_year, req_month_num = _parse_month_param(month, config.tzinfo)
    month_key = f"{req_year}-{req_month_num:02d}"

    try:
        await asyncio.to_thread(client.validate_auth)
    except JiraClientError as exc:
        client.close()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    now = datetime.now(config.tzinfo)
    start = datetime.combine(date(req_year, req_month_num, 1), time.min, config.tzinfo)
    # For the current month end at now; for past months end at last day of that month
    if req_year == now.year and req_month_num == now.month:
        end = now
    else:
        import calendar
        last_day = calendar.monthrange(req_year, req_month_num)[1]
        end = datetime.combine(date(req_year, req_month_num, last_day), time.max, config.tzinfo)

    suggestions = suggest_minutes_from_git(repo_path=Path(repo_path), since=start, until=end)
    weekdays = _weekdays_in_range(start.date(), end.date())
    if not weekdays:
        client.close()
        return MonthPlanResponse(month=month_key, timezone=config.timezone, days=[])

    (
        current_user_id,
        tempo_worklogs,
        prefetched_issues_by_key,
        prefetched_issue_worklogs_by_key,
        existing_prefetched,
        fully_logged_days,
    ) = await _prefetch_month_logged_context(client, config, weekdays)

    query_weekdays = [day for day in weekdays if day not in fully_logged_days]

    # Parallel primary JQL searches only for weekdays that still need planning.
    search_results: list[tuple[date, list[Issue], str | None]] = []
    if query_weekdays:
        t_search = _time.monotonic()
        search_semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)
        search_tasks = [_search_day_async(client, config, day, search_semaphore) for day in query_weekdays]
        search_results = await asyncio.gather(*search_tasks)
        logger.info("jql_searches_done days=%s elapsed=%.2fs", len(query_weekdays), _time.monotonic() - t_search)

    day_issues: dict[date, list[Issue]] = {day: [] for day in weekdays}
    all_issues: dict[str, Issue] = {issue.key: issue for issue in prefetched_issues_by_key.values()}
    query_errors: list[str] = []

    for day, issues, error in search_results:
        if error:
            query_errors.append(f"{day.isoformat()}: {error}")
            day_issues[day] = []
        else:
            day_issues[day] = issues
            for issue in issues:
                all_issues[issue.key] = issue

    if query_weekdays and query_errors and len(query_errors) == len(query_weekdays):
        client.close()
        raise HTTPException(status_code=400, detail=f"Month query failed for all weekdays. First error: {query_errors[0]}")

    # Secondary query: add tickets not already found by primary, with 0 min default.
    if config.secondary_tracking_statuses and query_weekdays:
        sec_semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)
        sec_tasks = [_search_day_secondary_async(client, config, day, sec_semaphore) for day in query_weekdays]
        sec_results = await asyncio.gather(*sec_tasks)
        for day, sec_issues, _ in sec_results:
            existing_day = day_issues.get(day, [])
            existing_keys = {i.key for i in existing_day}
            new_issues = [i for i in sec_issues if i.key not in existing_keys]
            if new_issues:
                day_issues[day] = existing_day + new_issues
                for issue in new_issues:
                    if issue.key not in all_issues:
                        all_issues[issue.key] = issue

    all_issues_list = list(all_issues.values())
    issue_daily_seed = _daily_seed_for_issues(all_issues_list, suggestions, len(weekdays))

    # Parallel worklog fetches for non-prefetched issues only.
    t_worklogs = _time.monotonic()
    issue_worklogs_by_key: dict[str, list[dict]] = dict(prefetched_issue_worklogs_by_key)
    existing: dict[tuple[str, date], tuple[int, int | None]] = dict(existing_prefetched)

    if current_user_id and all_issues_list:
        missing_issue_keys = [
            issue.key for issue in all_issues_list if issue.key not in issue_worklogs_by_key
        ]
        if missing_issue_keys:
            wl_semaphore = asyncio.Semaphore(WORKLOG_CONCURRENCY)
            wl_tasks = [_fetch_issue_worklogs_async(client, issue_key, wl_semaphore) for issue_key in missing_issue_keys]
            wl_results = await asyncio.gather(*wl_tasks)
            fetched_missing = dict(wl_results)
            issue_worklogs_by_key.update(fetched_missing)

            extra_existing = _build_existing_map_from_raw(
                current_user_id,
                fetched_missing,
                tempo_worklogs,
                weekdays[0],
                weekdays[-1],
                set(missing_issue_keys),
            )
            existing.update(extra_existing)

    logger.info(
        "worklog_fetches_done issues=%s elapsed=%.2fs",
        len(all_issues_list),
        _time.monotonic() - t_worklogs,
    )

    primary_keys_by_day: dict[date, set[str]] = {day: set() for day in weekdays}
    for primary_day, issues, _err in search_results:
        primary_keys_by_day[primary_day] = {issue.key for issue in issues}

    days: list[DayPlan] = []
    for day in weekdays:
        if day in fully_logged_days:
            days.append(_build_logged_day_plan_from_existing(day, existing, all_issues))
            continue

        entries: list[DayPlanEntry] = []
        primary_day_keys = primary_keys_by_day.get(day, set())
        for issue in day_issues.get(day, []):
            existing_result = existing.get((issue.key, day))
            is_secondary_only = issue.key not in primary_day_keys
            if existing_result is not None:
                existing_minutes, worklog_id = existing_result
                entries.append(
                    DayPlanEntry(
                        date=day,
                        issue_key=issue.key,
                        issue_type=issue.issue_type,
                        summary=issue.summary,
                        status=issue.status,
                        minutes=existing_minutes,
                        source="logged",
                        locked=False,
                        removed=False,
                        worklog_id=worklog_id,
                    )
                )
            elif is_secondary_only:
                entries.append(
                    DayPlanEntry(
                        date=day,
                        issue_key=issue.key,
                        issue_type=issue.issue_type,
                        summary=issue.summary,
                        status=issue.status,
                        minutes=0,
                        source="generated-secondary",
                        locked=False,
                        removed=False,
                        worklog_id=None,
                    )
                )
            else:
                entries.append(
                    DayPlanEntry(
                        date=day,
                        issue_key=issue.key,
                        issue_type=issue.issue_type,
                        summary=issue.summary,
                        status=issue.status,
                        minutes=issue_daily_seed.get(issue.key, 0),
                        source=suggestions.get(issue.key).source if issue.key in suggestions else "generated",
                        locked=False,
                        removed=False,
                        worklog_id=None,
                    )
                )

        days.append(DayPlan(date=day, entries=entries, total_minutes=sum(e.minutes for e in entries)))

    balanced_days = rebalance_month(days)
    client.close()
    logger.info("get_month_plan_done total_elapsed=%.2fs", _time.monotonic() - t0)
    return MonthPlanResponse(month=month_key, timezone=config.timezone, days=balanced_days)


def _serialize_day_plan(d: DayPlan) -> dict:
    return {
        "date": d.date.isoformat(),
        "entries": [
            {
                "date": e.date.isoformat(),
                "issue_key": e.issue_key,
                "issue_type": e.issue_type,
                "summary": e.summary,
                "status": e.status,
                "minutes": e.minutes,
                "source": e.source,
                "locked": e.locked,
                "removed": e.removed,
                "worklog_id": e.worklog_id,
            }
            for e in d.entries
        ],
        "total_minutes": d.total_minutes,
    }


@app.get("/api/month/plan/stream")
async def stream_month_plan(repo_path: str = ".", month: str | None = None) -> StreamingResponse:
    async def generate() -> AsyncGenerator[str, None]:
        t0 = _time.monotonic()
        try:
            config = load_config()
            client = JiraClient(config)
        except (ConfigError, SessionAuthError) as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
            return

        try:
            req_year, req_month_num = _parse_month_param(month, config.tzinfo)
        except HTTPException as exc:
            yield f"event: error\ndata: {json.dumps({'detail': exc.detail})}\n\n"
            return

        month_key = f"{req_year}-{req_month_num:02d}"

        try:
            await asyncio.to_thread(client.validate_auth)
        except JiraClientError as exc:
            client.close()
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
            return

        now = datetime.now(config.tzinfo)
        start = datetime.combine(date(req_year, req_month_num, 1), time.min, config.tzinfo)
        if req_year == now.year and req_month_num == now.month:
            end = now
        else:
            import calendar
            last_day = calendar.monthrange(req_year, req_month_num)[1]
            end = datetime.combine(date(req_year, req_month_num, last_day), time.max, config.tzinfo)

        suggestions = suggest_minutes_from_git(repo_path=Path(repo_path), since=start, until=end)
        weekdays = _weekdays_in_range(start.date(), end.date())

        if not weekdays:
            client.close()
            payload = json.dumps({"month": month_key, "timezone": config.timezone, "days": []})
            yield f"event: complete\ndata: {payload}\n\n"
            return

        (
            current_user_id,
            tempo_worklogs,
            prefetched_issues_by_key,
            prefetched_issue_worklogs_by_key,
            existing_prefetched,
            fully_logged_days,
        ) = await _prefetch_month_logged_context(client, config, weekdays)

        query_weekdays = [day for day in weekdays if day not in fully_logged_days]

        all_day_issues: dict[date, list[Issue]] = {day: [] for day in weekdays}
        all_issues_accumulated: dict[str, Issue] = {issue.key: issue for issue in prefetched_issues_by_key.values()}
        existing_global: dict[tuple[str, date], tuple[int, int | None]] = dict(existing_prefetched)
        issue_worklogs_cache: dict[str, list[dict]] = dict(prefetched_issue_worklogs_by_key)
        all_days: list[DayPlan] = []
        query_errors: list[str] = []
        ready_emitted_dates: set[date] = set()

        skipped_days = [day for day in weekdays if day in fully_logged_days]
        if skipped_days:
            skipped_plans = [
                _build_logged_day_plan_from_existing(day, existing_global, all_issues_accumulated)
                for day in skipped_days
            ]
            all_days.extend(skipped_plans)
            skipped_chunk_payload = json.dumps({"days": [_serialize_day_plan(d) for d in skipped_plans]})
            yield f"event: chunk\ndata: {skipped_chunk_payload}\n\n"

            for day in skipped_days:
                ready_emitted_dates.add(day)
                ready_payload = json.dumps({"date": day.isoformat()})
                yield f"event: day-ready\ndata: {ready_payload}\n\n"

        for batch_start in range(0, len(query_weekdays), SEARCH_CONCURRENCY):
            batch = query_weekdays[batch_start : batch_start + SEARCH_CONCURRENCY]

            t_batch = _time.monotonic()
            semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)
            search_tasks = [_search_day_async(client, config, day, semaphore) for day in batch]
            search_results = await asyncio.gather(*search_tasks)

            batch_issues: dict[str, Issue] = {}
            for day, issues, error in search_results:
                if error:
                    query_errors.append(f"{day.isoformat()}: {error}")
                    all_day_issues[day] = []
                    continue

                all_day_issues[day] = issues
                for issue in issues:
                    batch_issues[issue.key] = issue
                    all_issues_accumulated[issue.key] = issue

            if current_user_id and batch_issues:
                missing_batch_keys = [key for key in batch_issues if key not in issue_worklogs_cache]
                if missing_batch_keys:
                    wl_semaphore = asyncio.Semaphore(WORKLOG_CONCURRENCY)
                    wl_tasks = [_fetch_issue_worklogs_async(client, issue_key, wl_semaphore) for issue_key in missing_batch_keys]
                    wl_results = await asyncio.gather(*wl_tasks)
                    fetched_missing = dict(wl_results)
                    issue_worklogs_cache.update(fetched_missing)

                    existing_batch = _build_existing_map_from_raw(
                        current_user_id,
                        fetched_missing,
                        tempo_worklogs,
                        batch[0],
                        batch[-1],
                        set(fetched_missing.keys()),
                    )
                    existing_global.update(existing_batch)

            all_issues_list = list(all_issues_accumulated.values())
            issue_daily_seed = _daily_seed_for_issues(all_issues_list, suggestions, len(weekdays))

            batch_days: list[DayPlan] = []
            for day in batch:
                entries: list[DayPlanEntry] = []
                for issue in all_day_issues.get(day, []):
                    existing_result = existing_global.get((issue.key, day))
                    if existing_result is not None:
                        existing_minutes, worklog_id = existing_result
                        entries.append(
                            DayPlanEntry(
                                date=day,
                                issue_key=issue.key,
                                issue_type=issue.issue_type,
                                summary=issue.summary,
                                status=issue.status,
                                minutes=existing_minutes,
                                source="logged",
                                locked=False,
                                removed=False,
                                worklog_id=worklog_id,
                            )
                        )
                    else:
                        entries.append(
                            DayPlanEntry(
                                date=day,
                                issue_key=issue.key,
                                issue_type=issue.issue_type,
                                summary=issue.summary,
                                status=issue.status,
                                minutes=issue_daily_seed.get(issue.key, 0),
                                source=suggestions.get(issue.key).source if issue.key in suggestions else "generated",
                                locked=False,
                                removed=False,
                                worklog_id=None,
                            )
                        )

                day_plan = rebalance_day(DayPlan(date=day, entries=entries, total_minutes=sum(e.minutes for e in entries)))
                batch_days.append(day_plan)
                all_days.append(day_plan)

            logger.info(
                "stream batch_done batch_start=%s batch_size=%s elapsed=%.2fs",
                batch_start,
                len(batch),
                _time.monotonic() - t_batch,
            )

            chunk_payload = json.dumps({"days": [_serialize_day_plan(d) for d in batch_days]})
            yield f"event: chunk\ndata: {chunk_payload}\n\n"

            if not config.secondary_tracking_statuses:
                for day in batch:
                    if day in ready_emitted_dates:
                        continue
                    ready_emitted_dates.add(day)
                    ready_payload = json.dumps({"date": day.isoformat()})
                    yield f"event: day-ready\ndata: {ready_payload}\n\n"

        if query_weekdays and query_errors and len(query_errors) == len(query_weekdays):
            client.close()
            detail = f"Month query failed for all weekdays. First error: {query_errors[0]}"
            yield f"event: error\ndata: {json.dumps({'detail': detail})}\n\n"
            return

        if config.secondary_tracking_statuses:
            day_plan_by_date: dict[date, DayPlan] = {day_plan.date: day_plan for day_plan in all_days}

            for batch_start in range(0, len(query_weekdays), SEARCH_CONCURRENCY):
                batch = query_weekdays[batch_start : batch_start + SEARCH_CONCURRENCY]

                sec_semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)
                sec_tasks = [_search_day_secondary_async(client, config, day, sec_semaphore) for day in batch]
                sec_results = await asyncio.gather(*sec_tasks)

                new_secondary_by_day: dict[date, list[Issue]] = {}
                new_secondary_keys: set[str] = set()
                for day, sec_issues, _ in sec_results:
                    existing_keys = {issue.key for issue in all_day_issues.get(day, [])}
                    filtered = [issue for issue in sec_issues if issue.key not in existing_keys]
                    if not filtered:
                        continue

                    new_secondary_by_day[day] = filtered
                    all_day_issues[day] = all_day_issues.get(day, []) + filtered
                    for issue in filtered:
                        all_issues_accumulated[issue.key] = issue
                        new_secondary_keys.add(issue.key)

                missing_secondary_keys = [key for key in new_secondary_keys if key not in issue_worklogs_cache]
                if current_user_id and missing_secondary_keys:
                    wl_semaphore = asyncio.Semaphore(WORKLOG_CONCURRENCY)
                    wl_tasks = [_fetch_issue_worklogs_async(client, issue_key, wl_semaphore) for issue_key in missing_secondary_keys]
                    wl_results = await asyncio.gather(*wl_tasks)
                    fetched_missing = dict(wl_results)
                    issue_worklogs_cache.update(fetched_missing)

                    sec_existing = _build_existing_map_from_raw(
                        current_user_id,
                        fetched_missing,
                        tempo_worklogs,
                        batch[0],
                        batch[-1],
                        set(fetched_missing.keys()),
                    )
                    existing_global.update(sec_existing)

                updated_batch_days: list[DayPlan] = []
                for day in batch:
                    existing_plan = day_plan_by_date.get(day)
                    existing_entries = list(existing_plan.entries) if existing_plan else []
                    newly_added = new_secondary_by_day.get(day, [])

                    if newly_added:
                        for issue in newly_added:
                            existing_result = existing_global.get((issue.key, day))
                            if existing_result is not None:
                                existing_minutes, worklog_id = existing_result
                                existing_entries.append(
                                    DayPlanEntry(
                                        date=day,
                                        issue_key=issue.key,
                                        issue_type=issue.issue_type,
                                        summary=issue.summary,
                                        status=issue.status,
                                        minutes=existing_minutes,
                                        source="logged",
                                        locked=False,
                                        removed=False,
                                        worklog_id=worklog_id,
                                    )
                                )
                            else:
                                existing_entries.append(
                                    DayPlanEntry(
                                        date=day,
                                        issue_key=issue.key,
                                        issue_type=issue.issue_type,
                                        summary=issue.summary,
                                        status=issue.status,
                                        minutes=0,
                                        source="generated-secondary",
                                        locked=False,
                                        removed=False,
                                        worklog_id=None,
                                    )
                                )

                    updated_plan = rebalance_day(
                        DayPlan(
                            date=day,
                            entries=existing_entries,
                            total_minutes=sum(entry.minutes for entry in existing_entries),
                        )
                    )
                    day_plan_by_date[day] = updated_plan
                    for idx, item in enumerate(all_days):
                        if item.date == day:
                            all_days[idx] = updated_plan
                            break
                    else:
                        all_days.append(updated_plan)
                    updated_batch_days.append(updated_plan)

                if updated_batch_days and any(day in new_secondary_by_day for day in batch):
                    chunk_payload = json.dumps({"days": [_serialize_day_plan(d) for d in updated_batch_days]})
                    yield f"event: chunk\ndata: {chunk_payload}\n\n"

                for day in batch:
                    if day in ready_emitted_dates:
                        continue
                    ready_emitted_dates.add(day)
                    ready_payload = json.dumps({"date": day.isoformat()})
                    yield f"event: day-ready\ndata: {ready_payload}\n\n"

        # ── Final: rebalance and complete ────────────────────────────────────
        all_days_sorted = sorted(all_days, key=lambda day_plan: day_plan.date)
        balanced_days = rebalance_month(all_days_sorted)
        complete_payload = json.dumps({
            "month": month_key,
            "timezone": config.timezone,
            "days": [_serialize_day_plan(d) for d in balanced_days],
        })
        client.close()
        logger.info("stream_month_plan_done total_elapsed=%.2fs", _time.monotonic() - t0)
        yield f"event: complete\ndata: {complete_payload}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/month/rebalance", response_model=MonthPlanResponse)
def rebalance(payload: RebalanceRequest) -> MonthPlanResponse:
    balanced = rebalance_month(payload.days)
    return MonthPlanResponse(month=payload.month, timezone=payload.timezone, days=balanced)


@app.post("/api/month/submit", response_model=SubmitResponse)
def submit(payload: SubmitRequest) -> SubmitResponse:
    try:
        config = load_config()
        client = JiraClient(config)
    except (ConfigError, SessionAuthError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    results: list[SubmitItemResult] = []

    for day in payload.days:
        weekday_total = sum(entry.minutes for entry in day.entries if not entry.removed)
        if day.date.weekday() < 5 and weekday_total > DAILY_TARGET_MINUTES:
            client.close()
            raise HTTPException(
                status_code=400,
                detail=f"Day {day.date.isoformat()} exceeds 8h ({weekday_total} minutes).",
            )

        for entry in day.entries:
            if entry.removed or entry.minutes <= 0:
                continue

            issue = Issue(
                key=entry.issue_key,
                issue_type=entry.issue_type,
                summary=entry.summary,
                status=entry.status,
            )
            started_at = datetime.combine(day.date, time(9, 0), config.tzinfo)
            worklog = WorklogEntry(
                issue=issue,
                time_minutes=entry.minutes,
                comment=None,
                started_at=started_at,
            )

            if entry.worklog_id is not None:
                # Try to update the existing Jira worklog; fall back to create on failure.
                try:
                    client.update_worklog(entry.worklog_id, worklog)
                    results.append(
                        SubmitItemResult(
                            date=day.date,
                            issue_key=entry.issue_key,
                            minutes=entry.minutes,
                            success=True,
                            message="updated",
                        )
                    )
                    continue
                except JiraClientError:
                    pass  # Fall through to create a new worklog.

            try:
                client.create_worklog(worklog)
                results.append(
                    SubmitItemResult(
                        date=day.date,
                        issue_key=entry.issue_key,
                        minutes=entry.minutes,
                        success=True,
                        message="submitted",
                    )
                )
            except JiraClientError as exc:
                results.append(
                    SubmitItemResult(
                        date=day.date,
                        issue_key=entry.issue_key,
                        minutes=entry.minutes,
                        success=False,
                        message=str(exc),
                    )
                )

    client.close()
    return SubmitResponse(results=results)


@app.post("/api/day/refresh", response_model=DayPlan)
async def refresh_day(payload: DayRefreshRequest) -> DayPlan:
    """Re-fetch issues and worklogs for a single day and return a fresh DayPlan.

    Semantics mirror the month plan: if the current user has logged work for any
    (issue, date) bucket, those entries come back with source="logged".  Otherwise
    entries are generated and balanced to an 8-hour target via rebalance_day().
    """
    try:
        target_date = date.fromisoformat(payload.date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date '{payload.date}': {exc}") from exc

    if target_date.weekday() >= 5:
        raise HTTPException(status_code=400, detail=f"Day {payload.date} is a weekend; refresh is only supported for weekdays.")

    try:
        config = load_config()
        client = JiraClient(config)
    except (ConfigError, SessionAuthError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        await asyncio.to_thread(client.validate_auth)
    except JiraClientError as exc:
        client.close()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        # 1. Fetch issues for this day
        jql = build_jql_for_day_snapshot(config, target_date)
        issues = await asyncio.to_thread(client.search_issues, jql=jql)
        issues = _dedupe_issues_by_key(issues)

        # 2. Fetch current user identity
        current_user_id: str | None = None
        try:
            current_user_id = await asyncio.to_thread(client.get_current_user_account_id)
        except JiraClientError:
            pass

        # 3. Parallel worklog fetch for all issues on this day
        issue_worklogs_by_key: dict[str, list[dict]] = {}
        if current_user_id:
            wl_semaphore = asyncio.Semaphore(WORKLOG_CONCURRENCY)
            wl_tasks = [_fetch_issue_worklogs_async(client, issue.key, wl_semaphore) for issue in issues]
            wl_results = await asyncio.gather(*wl_tasks)
            issue_worklogs_by_key = dict(wl_results)

        # 4. Tempo worklogs for this day
        tempo_worklogs: list[dict] = []
        if current_user_id:
            try:
                _uid = current_user_id
                tempo_worklogs = await asyncio.to_thread(
                    lambda: client.get_tempo_worklogs_for_user(
                        account_id=_uid,
                        from_date=target_date,
                        to_date=target_date,
                    )
                )
            except JiraClientError:
                pass

        # 5. Build existing (issue, day) -> (minutes, worklog_id) map
        primary_keys = {issue.key for issue in issues}
        issue_keys = set(primary_keys)
        existing: dict[tuple[str, date], tuple[int, int | None]] = {}
        if current_user_id:
            existing = _build_existing_map_from_raw(
                current_user_id,
                issue_worklogs_by_key,
                tempo_worklogs,
                target_date,
                target_date,
                issue_keys,
            )

        # 5b. Secondary status query — add tickets not in primary feed for this day
        if config.secondary_tracking_statuses:
            sec_jql = build_jql_for_day_secondary_snapshot(config, target_date)
            if sec_jql:
                try:
                    sec_issues_raw = await asyncio.to_thread(client.search_issues, jql=sec_jql)
                    sec_issues = [i for i in _dedupe_issues_by_key(sec_issues_raw) if i.key not in primary_keys]
                except JiraClientError:
                    sec_issues = []

                if sec_issues:
                    if current_user_id:
                        sec_wl_semaphore = asyncio.Semaphore(WORKLOG_CONCURRENCY)
                        sec_wl_tasks = [_fetch_issue_worklogs_async(client, i.key, sec_wl_semaphore) for i in sec_issues]
                        sec_wl_results = await asyncio.gather(*sec_wl_tasks)
                        sec_worklogs_by_key = dict(sec_wl_results)
                        sec_existing = _build_existing_map_from_raw(
                            current_user_id,
                            sec_worklogs_by_key,
                            tempo_worklogs,
                            target_date,
                            target_date,
                            {i.key for i in sec_issues},
                        )
                        existing.update(sec_existing)
                    issues = issues + sec_issues
                    issue_keys = {i.key for i in issues}

        # 6. Build entries: logged wins; primary-generated get even 8h allocation;
        #    secondary-only entries default to 0.
        non_logged = [iss for iss in issues if iss.key in primary_keys and (iss.key, target_date) not in existing]
        per_issue_minutes = DAILY_TARGET_MINUTES // len(non_logged) if non_logged else 0
        # Round down to nearest 15 minutes
        per_issue_minutes = (per_issue_minutes // 15) * 15

        entries: list[DayPlanEntry] = []
        for issue in issues:
            existing_result = existing.get((issue.key, target_date))
            if existing_result is not None:
                existing_minutes, worklog_id = existing_result
                entries.append(DayPlanEntry(
                    date=target_date,
                    issue_key=issue.key,
                    issue_type=issue.issue_type,
                    summary=issue.summary,
                    status=issue.status,
                    minutes=existing_minutes,
                    source="logged",
                    locked=False,
                    removed=False,
                    worklog_id=worklog_id,
                ))
            else:
                entries.append(DayPlanEntry(
                    date=target_date,
                    issue_key=issue.key,
                    issue_type=issue.issue_type,
                    summary=issue.summary,
                    status=issue.status,
                    minutes=per_issue_minutes if issue.key in primary_keys else 0,
                    source="generated" if issue.key in primary_keys else "generated-secondary",
                    locked=False,
                    removed=False,
                    worklog_id=None,
                ))

        raw_day = DayPlan(
            date=target_date,
            entries=entries,
            total_minutes=sum(e.minutes for e in entries),
        )
        result_day = rebalance_day(raw_day)

    finally:
        client.close()

    return result_day


@app.post("/api/day/tickets", response_model=DayPlanEntry)
async def add_day_ticket(payload: DayAddTicketRequest) -> DayPlanEntry:
    try:
        target_date = date.fromisoformat(payload.date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date '{payload.date}': {exc}") from exc

    if target_date.weekday() >= 5:
        raise HTTPException(status_code=400, detail=f"Day {payload.date} is a weekend; adding tickets is only supported for weekdays.")

    issue_key = payload.issue_key.strip().upper()
    if not ISSUE_KEY_PATTERN.match(issue_key):
        raise HTTPException(status_code=422, detail="Invalid ticket key format. Expected format like PROJ-123.")

    try:
        config = load_config()
        client = JiraClient(config)
    except (ConfigError, SessionAuthError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        await asyncio.to_thread(client.validate_auth)

        issues = await asyncio.to_thread(client.search_issues, jql=f'key = "{issue_key}"', max_results=1)
        issue = next((item for item in issues if item.key.upper() == issue_key), None)
        if issue is None:
            raise HTTPException(status_code=404, detail=f"Ticket {issue_key} was not found in Jira.")

        current_user_id: str | None = None
        try:
            current_user_id = await asyncio.to_thread(client.get_current_user_account_id)
        except JiraClientError:
            pass

        existing: dict[tuple[str, date], tuple[int, int | None]] = {}
        if current_user_id:
            issue_key_worklogs, worklogs = await _fetch_issue_worklogs_async(
                client,
                issue.key,
                asyncio.Semaphore(1),
            )
            issue_worklogs_by_key = {issue_key_worklogs: worklogs}

            tempo_worklogs: list[dict] = []
            try:
                _uid = current_user_id
                tempo_worklogs = await asyncio.to_thread(
                    lambda: client.get_tempo_worklogs_for_user(
                        account_id=_uid,
                        from_date=target_date,
                        to_date=target_date,
                    )
                )
            except JiraClientError:
                pass

            existing = _build_existing_map_from_raw(
                current_user_id,
                issue_worklogs_by_key,
                tempo_worklogs,
                target_date,
                target_date,
                {issue.key},
            )

        existing_result = existing.get((issue.key, target_date))
        if existing_result is not None:
            existing_minutes, worklog_id = existing_result
            return DayPlanEntry(
                date=target_date,
                issue_key=issue.key,
                issue_type=issue.issue_type,
                summary=issue.summary,
                status=issue.status,
                minutes=existing_minutes,
                source="logged",
                locked=False,
                removed=False,
                worklog_id=worklog_id,
            )

        return DayPlanEntry(
            date=target_date,
            issue_key=issue.key,
            issue_type=issue.issue_type,
            summary=issue.summary,
            status=issue.status,
            minutes=0,
            source="generated-manual",
            locked=False,
            removed=False,
            worklog_id=None,
        )
    except JiraClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        client.close()


def _daily_seed_for_issues(
    issues: list[Issue],
    suggestions: dict,
    weekdays_count: int,
) -> dict[str, int]:
    if not issues or weekdays_count <= 0:
        return {}

    seeds: dict[str, int] = {}
    total = 0
    for issue in issues:
        if _is_spike_story_issue(issue):
            seeds[issue.key] = 0
            continue

        if issue.key in suggestions:
            minutes = max(0, int(suggestions[issue.key].minutes / weekdays_count))
        else:
            minutes = 60
        seeds[issue.key] = minutes
        total += minutes

    if total == 0:
        allocatable = [issue for issue in issues if not _is_spike_story_issue(issue)]
        if allocatable:
            even = DAILY_TARGET_MINUTES // len(allocatable)
            remainder = DAILY_TARGET_MINUTES % len(allocatable)
            for idx, issue in enumerate(allocatable):
                seeds[issue.key] = even + (1 if idx < remainder else 0)
        for issue in issues:
            if _is_spike_story_issue(issue):
                seeds[issue.key] = 0

    return seeds



def _weekdays_in_range(start: date, end: date) -> list[date]:
    current = start
    values: list[date] = []
    while current <= end:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return values


def _is_spike_story_issue(issue: Issue) -> bool:
    return "story" in issue.issue_type.lower() and bool(re.search(r"\bSPIKE\b", issue.summary or ""))



def _existing_jira_worklogs_by_issue_day(
    client: JiraClient,
    issues: list[Issue],
    start_date: date,
    end_date: date,
) -> dict[tuple[str, date], tuple[int, int | None]]:
    """
    Returns a mapping of (issue_key, date) -> (total_minutes, jira_worklog_id).
    Primary source is Jira issue worklogs for current user. When available, Tempo
    worklogs are used as a fallback for keys/days not already observed from Jira.
    """
    if not issues:
        return {}

    issue_keys = {issue.key for issue in issues}

    try:
        current_user_id = client.get_current_user_account_id()
    except JiraClientError:
        return {}

    logger.info(
        "existing_worklogs_lookup start=%s end=%s issues=%s current_user=%s",
        start_date.isoformat(),
        end_date.isoformat(),
        len(issues),
        current_user_id,
    )

    issue_worklogs_by_key: dict[str, list[dict]] = {}
    for issue in issues:
        try:
            issue_worklogs_by_key[issue.key] = client.get_issue_worklogs(issue.key)
        except JiraClientError:
            issue_worklogs_by_key[issue.key] = []

    try:
        tempo_worklogs = client.get_tempo_worklogs_for_user(
            account_id=current_user_id,
            from_date=start_date,
            to_date=end_date,
        )
    except JiraClientError:
        tempo_worklogs = []

    logger.info(
        "existing_worklogs_tempo_raw count=%s sample=%s",
        len(tempo_worklogs),
        tempo_worklogs[:5],
    )

    return _build_existing_map_from_raw(
        current_user_id,
        issue_worklogs_by_key,
        tempo_worklogs,
        start_date,
        end_date,
        issue_keys,
    )


def _build_existing_map_from_raw(
    current_user_id: str,
    issue_worklogs_by_key: dict[str, list[dict]],
    tempo_worklogs: list[dict],
    start_date: date,
    end_date: date,
    issue_keys: set[str],
) -> dict[tuple[str, date], tuple[int, int | None]]:
    """
    Pure function: build (issue_key, date) -> (minutes, worklog_id) from pre-fetched data.
    Jira is authoritative; Tempo fills missing (issue, day) buckets only.
    """
    existing_minutes: dict[tuple[str, date], int] = defaultdict(int)
    existing_ids: dict[tuple[str, date], int | None] = {}

    current_user_identity = str(current_user_id or "").strip()

    for issue_key, worklogs in issue_worklogs_by_key.items():
        for item in worklogs:
            if not _worklog_belongs_to_current_user(current_user_identity, item):
                continue

            started = item.get("started") or item.get("startDate") or item.get("dateStarted")
            time_seconds = item.get("timeSpentSeconds", 0)
            try:
                time_seconds = int(time_seconds)
            except (TypeError, ValueError):
                time_seconds = 0
            if not started or time_seconds <= 0:
                continue

            try:
                started_date = date.fromisoformat(str(started)[:10])
            except ValueError:
                continue

            if started_date < start_date or started_date > end_date:
                continue

            key = (issue_key, started_date)
            existing_minutes[key] += time_seconds // 60
            if key not in existing_ids:
                raw_id = item.get("id")
                existing_ids[key] = int(raw_id) if raw_id is not None else None

    logger.info(
        "existing_worklogs_jira_buckets count=%s sample=%s",
        len(existing_minutes),
        list(existing_minutes.items())[:10],
    )

    tempo_minutes: dict[tuple[str, date], int] = defaultdict(int)
    for item in tempo_worklogs:
        issue_key = item.get("issueKey") or (item.get("issue") or {}).get("key")
        if not issue_key or str(issue_key) not in issue_keys:
            continue

        started = item.get("startDate") or item.get("dateStarted") or item.get("started")
        if not started:
            continue
        try:
            started_date = date.fromisoformat(str(started)[:10])
        except ValueError:
            continue
        if started_date < start_date or started_date > end_date:
            continue

        raw_seconds = item.get("timeSpentSeconds", 0)
        try:
            time_seconds = int(raw_seconds)
        except (TypeError, ValueError):
            continue
        if time_seconds <= 0:
            continue

        key = (str(issue_key), started_date)
        tempo_minutes[key] += time_seconds // 60

    for key, minutes in tempo_minutes.items():
        if key not in existing_minutes:
            existing_minutes[key] = minutes
            existing_ids[key] = None

    logger.info(
        "existing_worklogs_final_buckets count=%s sample=%s",
        len(existing_minutes),
        list(existing_minutes.items())[:10],
    )

    return {key: (existing_minutes[key], existing_ids.get(key)) for key in existing_minutes}


def _worklog_belongs_to_current_user(current_user_identity: str, item: dict) -> bool:
    """Match Jira worklogs to the current user as strictly as the payload allows.

    Prefer explicit accountId fields when the worklog provides them. Only fall back
    to legacy key/name/email fields when no accountId is present in that worklog.
    This avoids attributing another user's work when legacy fields happen to collide.
    """
    if not current_user_identity:
        return False

    author = item.get("author") or {}
    account_identities = {
        str(author.get("accountId") or "").strip(),
        str(item.get("authorAccountId") or "").strip(),
        str(item.get("accountId") or "").strip(),
    }
    account_identities.discard("")
    if account_identities:
        return current_user_identity in account_identities

    fallback_identities = {
        str(author.get("key") or "").strip(),
        str(item.get("authorKey") or "").strip(),
        str(author.get("name") or "").strip(),
        str(item.get("authorName") or "").strip(),
        str(author.get("emailAddress") or "").strip(),
    }
    fallback_identities.discard("")
    return current_user_identity in fallback_identities


def _dedupe_issues_by_key(issues: list[Issue]) -> list[Issue]:
    deduped: dict[str, Issue] = {}
    for issue in issues:
        if issue.key not in deduped:
            deduped[issue.key] = issue
    return list(deduped.values())



def run() -> None:
    try:
        config = load_config(require_auth=False)
        debug = config.debug
        logger.info(
            "startup auth_mode=sso session_path=%s session_exists=%s log_level=%s debug=%s",
            config.session_state_path,
            config.has_sso_session,
            "DEBUG" if debug else "INFO",
            debug,
        )
    except ConfigError as exc:
        logger.warning("startup config_error=%s", exc)
        debug = False
    
    host = os.getenv("JIRA_TRACK_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("JIRA_TRACK_WEB_PORT", "8000"))
    reload_enabled = _env_truthy("JIRA_TRACK_WEB_RELOAD", True)
    forwarded_allow_ips = os.getenv("JIRA_TRACK_WEB_FORWARDED_ALLOW_IPS", "127.0.0.1")

    # Pass log level to uvicorn
    uvicorn_log_level = "debug" if debug else "info"
    uvicorn.run(
        "jira_tracking_bot.web.app:app",
        host=host,
        port=port,
        reload=reload_enabled,
        log_level=uvicorn_log_level,
        proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips,
    )
