from __future__ import annotations

from datetime import date
import logging
from urllib.parse import urlencode

import httpx

from jira_tracking_bot.config import AppConfig
from jira_tracking_bot.models import Issue, WorklogEntry


logger = logging.getLogger(__name__)


class JiraClientError(RuntimeError):
    """Raised for Jira request failures."""


class JiraClient:
    def __init__(self, config: AppConfig, timeout_seconds: int = 20) -> None:
        self._base_url = config.jira_base_url
        self._api_base = f"{self._base_url}/rest/api/3"
        self._tempo_api_base = config.tempo_api_base.rstrip("/")
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._jira_client: httpx.Client | None = None

        if config.has_jira_credentials:
            self._jira_client = httpx.Client(
                base_url=self._api_base,
                headers={"Accept": "application/json"},
                auth=(config.jira_email, config.jira_token),
                timeout=timeout_seconds,
            )

        self._tempo_client: httpx.Client | None = None
        if config.tempo_api_token:
            self._tempo_client = httpx.Client(
                base_url=self._tempo_api_base,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {config.tempo_api_token}",
                },
                timeout=timeout_seconds,
            )

    def close(self) -> None:
        if self._jira_client is not None:
            self._jira_client.close()
        if self._tempo_client is not None:
            self._tempo_client.close()

    @property
    def auth_mode(self) -> str:
        return "api-token"

    @property
    def api_base_url(self) -> str:
        return f"{self._api_base}/"

    def validate_auth(self) -> None:
        response = self._request("GET", "/myself")
        self._ensure_ok(response, "Failed to validate Jira authentication")

    def probe(self, path: str, authenticated: bool = True) -> tuple[int, str, str]:
        if authenticated:
            response = self._request("GET", path)
        else:
            with httpx.Client(base_url=self.api_base_url, headers={"Accept": "application/json"}, timeout=20) as client:
                response = client.get(path)

        content_type = response.headers.get("content-type", "")
        body = response.text.strip().replace("\n", " ")[:200]
        return response.status_code, content_type, body

    def search_issues(self, *, jql: str, max_results: int = 100) -> list[Issue]:
        query = urlencode(
            {
                "jql": jql,
                "maxResults": max_results,
                "fields": "summary,issuetype,status",
            }
        )
        response = self._request("GET", f"/search/jql?{query}")
        self._ensure_ok(response, "Jira search failed")

        payload = response.json()
        issues_raw = payload.get("issues", [])
        issues: list[Issue] = []
        for issue_raw in issues_raw:
            fields = issue_raw.get("fields", {})
            issue_type = fields.get("issuetype", {}).get("name", "Unknown")
            status = fields.get("status", {}).get("name", "Unknown")
            summary = fields.get("summary", "")
            key = issue_raw.get("key", "")
            issue_id = issue_raw.get("id")
            if key:
                issues.append(Issue(key=key, issue_type=issue_type, summary=summary, status=status, issue_id=str(issue_id) if issue_id else None))
        return issues

    def search_issues_page(self, *, jql: str, max_results: int = 100, start_at: int = 0) -> tuple[list[Issue], int]:
        """Search Jira issues returning one page and the server-reported total."""
        query = urlencode(
            {
                "jql": jql,
                "startAt": start_at,
                "maxResults": max_results,
                "fields": "summary,issuetype,status",
            }
        )
        response = self._request("GET", f"/search/jql?{query}")
        self._ensure_ok(response, "Jira search failed")

        payload = response.json()
        total_raw = payload.get("total", 0)
        try:
            total = int(total_raw)
        except (TypeError, ValueError):
            total = 0

        issues_raw = payload.get("issues", [])
        issues: list[Issue] = []
        for issue_raw in issues_raw:
            fields = issue_raw.get("fields", {})
            issue_type = fields.get("issuetype", {}).get("name", "Unknown")
            status = fields.get("status", {}).get("name", "Unknown")
            summary = fields.get("summary", "")
            key = issue_raw.get("key", "")
            issue_id = issue_raw.get("id")
            if key:
                issues.append(
                    Issue(
                        key=key,
                        issue_type=issue_type,
                        summary=summary,
                        status=status,
                        issue_id=str(issue_id) if issue_id else None,
                    )
                )

        return issues, total

    def get_issue_worklogs(self, issue_key: str) -> list[dict]:
        worklogs: list[dict] = []
        start_at = 0
        max_results = 100

        while True:
            response = self._request("GET", f"/issue/{issue_key}/worklog?startAt={start_at}&maxResults={max_results}")
            self._ensure_ok(response, f"Failed to fetch worklogs for {issue_key}")
            payload = response.json()
            page = payload.get("worklogs", [])
            if not isinstance(page, list):
                break

            logger.info(
                "jira_worklogs_response issue=%s page_start=%s page_size=%s total=%s sample=%s",
                issue_key,
                start_at,
                len(page),
                payload.get("total"),
                page[:2],
            )

            worklogs.extend(page)
            total = int(payload.get("total", len(worklogs)))
            fetched = int(payload.get("maxResults", len(page)))
            start_at += fetched
            if start_at >= total or not page:
                break

        return worklogs

    def get_current_user_account_id(self) -> str:
        response = self._request("GET", "/myself")
        self._ensure_ok(response, "Failed to fetch current Jira user")
        payload = response.json()
        account_id = payload.get("accountId") or payload.get("name") or payload.get("key")
        if not account_id:
            raise JiraClientError("Jira /myself did not return a user account identifier")
        return str(account_id)

    def get_tempo_worklogs_for_user(
        self,
        *,
        account_id: str,
        from_date: date,
        to_date: date,
    ) -> list[dict]:
        """Fetch all Tempo worklogs for a given user in the date range (single API call)."""
        if self._tempo_client is None:
            return []

        all_items: list[dict] = []
        offset = 0
        limit = 1000

        while True:
            response = self._tempo_client.get(
                "/worklogs",
                params={
                    "accountId": account_id,
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    "limit": limit,
                    "offset": offset,
                },
            )
            if not 200 <= response.status_code < 300:
                raise JiraClientError(
                    f"Failed to fetch Tempo worklogs for user {account_id}. "
                    f"HTTP {response.status_code}. {response.text}"
                )

            payload = response.json()
            if isinstance(payload, list):
                logger.info(
                    "tempo_worklogs_response account_id=%s offset=%s result_count=%s sample=%s",
                    account_id,
                    offset,
                    len(payload),
                    payload[:2],
                )
                all_items.extend(payload)
                break

            if isinstance(payload, dict):
                results = payload.get("results", [])
                logger.info(
                    "tempo_worklogs_response account_id=%s offset=%s result_count=%s metadata=%s sample=%s",
                    account_id,
                    offset,
                    len(results) if isinstance(results, list) else 0,
                    payload.get("metadata"),
                    results[:2] if isinstance(results, list) else results,
                )
                if isinstance(results, list):
                    all_items.extend(results)
                metadata = payload.get("metadata") or {}
                total = metadata.get("count") or metadata.get("total") or len(all_items)
                if offset + limit >= int(total):
                    break
                offset += limit
            else:
                break

        return all_items

    def create_worklog(self, entry: WorklogEntry) -> None:
        # Jira Cloud expects the worklog body to be a valid JSON document object for
        # the comment field, not a plain string. The same pattern works for the
        # common documented Cloud format and remains compatible with the timezone
        # timestamp representation Jira accepts.
        started_value = entry.started_at.strftime("%Y-%m-%dT%H:%M:%S.000%z")
        payload = {
            "comment": self._worklog_comment_payload(entry.comment, entry.issue.key),
            "started": started_value,
            "timeSpentSeconds": entry.time_minutes * 60,
        }
        response = self._request(
            "POST",
            f"/issue/{entry.issue.key}/worklog",
            json=payload,
        )
        self._ensure_ok(response, f"Failed to log work for {entry.issue.key}")

    def update_worklog(self, worklog_id: int, entry: WorklogEntry) -> None:
        started_value = entry.started_at.strftime("%Y-%m-%dT%H:%M:%S.000%z")
        payload = {
            "comment": self._worklog_comment_payload(entry.comment, entry.issue.key),
            "started": started_value,
            "timeSpentSeconds": entry.time_minutes * 60,
        }
        response = self._request(
            "PUT",
            f"/issue/{entry.issue.key}/worklog/{worklog_id}",
            json=payload,
        )
        self._ensure_ok(response, f"Failed to update worklog {worklog_id} for {entry.issue.key}")

    def update_tempo_worklog(self, worklog_id: int, entry: WorklogEntry) -> None:
        """Update an existing Tempo worklog by its Tempo ID."""
        if self._tempo_client is None:
            raise JiraClientError("Tempo API token not configured; cannot update worklog")

        response = self._tempo_client.put(
            f"/worklogs/{worklog_id}",
            json={
                "issueKey": entry.issue.key,
                "timeSpentSeconds": entry.time_minutes * 60,
                "startDate": entry.started_at.strftime("%Y-%m-%d"),
                "startTime": entry.started_at.strftime("%H:%M:%S"),
                "description": self._default_worklog_comment(entry.issue.key),
            },
        )
        if not 200 <= response.status_code < 300:
            raise JiraClientError(
                f"Failed to update Tempo worklog {worklog_id}. HTTP {response.status_code}. {response.text}"
            )

    def _request(self, method: str, path: str, json: dict | None = None) -> httpx.Response:
        if self._jira_client is None:
            raise JiraClientError(
                "Jira credentials are not configured. Set JIRA_TRACK_JIRA_EMAIL and JIRA_TRACK_JIRA_TOKEN."
            )
        if self._config.debug:
            logger.debug("jira_request method=%s path=%s json=%s", method, path, json)

        response = self._jira_client.request(method=method, url=path, json=json)

        if self._config.debug:
            response_body = response.text.strip().replace("\n", " ")[:300]
            logger.debug(
                "jira_response method=%s path=%s status=%s content_type=%s body=%s",
                method,
                path,
                response.status_code,
                response.headers.get("content-type", ""),
                response_body,
            )

        return response

    @staticmethod
    def _default_worklog_comment(issue_key: str) -> str:
        return f"Working on issue {issue_key}"

    @classmethod
    def _worklog_comment_payload(cls, comment: str | None, issue_key: str) -> dict[str, object]:
        text = (comment or cls._default_worklog_comment(issue_key)).strip()
        if not text:
            text = cls._default_worklog_comment(issue_key)
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        }

    @staticmethod
    def _looks_like_upstream_proxy_block(response: httpx.Response) -> bool:
        details = response.text
        content_type = response.headers.get("content-type", "")
        return response.status_code == 403 and (
            "text/html" in content_type.lower() or "<html" in details.lower() or "access denied" in details.lower()
        )

    @staticmethod
    def _ensure_ok(response: httpx.Response, context: str) -> None:
        if 200 <= response.status_code < 300:
            return

        details = response.text
        content_type = response.headers.get("content-type", "")
        if JiraClient._looks_like_upstream_proxy_block(response):
            host = str(response.request.url)
            message = (
                f"{context}. HTTP 403 from an upstream proxy instead of Jira JSON. "
                f"This usually means one of: the Jira base URL is wrong, your API token is invalid, "
                f"or the request is hitting the wrong host/protocol. Checked URL: {host}."
            )
            raise JiraClientError(message)

        if response.status_code == 404:
            host = str(response.request.url)
            message = (
                f"{context}. HTTP 404. This often means the Jira REST path is not available on this instance or the base URL is incorrect. "
                f"Checked URL: {host}"
            )
            raise JiraClientError(message)

        message = f"{context}. HTTP {response.status_code}. {details}"
        raise JiraClientError(message)
