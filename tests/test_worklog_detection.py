from datetime import date

from jira_tracking_bot.models import Issue
from jira_tracking_bot.web.app import _existing_jira_worklogs_by_issue_day


class FakeClient:
    def get_current_user_account_id(self) -> str:
        return "user-123"

    def get_issue_worklogs(self, issue_key: str) -> list[dict]:
        if issue_key == "PROJ-1":
            return [
                {
                    "id": "101",
                    "author": {"accountId": "user-123"},
                    "started": "2026-04-01T09:00:00.000-0300",
                    "timeSpentSeconds": 7200,
                },
                {
                    "id": "102",
                    "author": {"accountId": "user-123"},
                    "started": "2026-04-01T13:00:00.000-0300",
                    "timeSpentSeconds": 1800,
                },
                {
                    "id": "103",
                    "author": {"accountId": "other-user"},
                    "started": "2026-04-01T15:00:00.000-0300",
                    "timeSpentSeconds": 3600,
                },
            ]
        return []

    def get_tempo_worklogs_for_user(self, *, account_id: str, from_date: date, to_date: date) -> list[dict]:
        return []


def test_existing_jira_worklogs_by_issue_day_aggregates_current_user_only() -> None:
    issues = [Issue(key="PROJ-1", issue_type="Bug", summary="Bug summary")]

    result = _existing_jira_worklogs_by_issue_day(
        FakeClient(),
        issues,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    assert result[("PROJ-1", date(2026, 4, 1))] == (150, 101)


def test_existing_jira_worklogs_by_issue_day_uses_tempo_for_missing_jira_days() -> None:
    class TempoOnlyClient(FakeClient):
        def get_issue_worklogs(self, issue_key: str) -> list[dict]:
            return []

        def get_tempo_worklogs_for_user(self, *, account_id: str, from_date: date, to_date: date) -> list[dict]:
            assert account_id == "user-123"
            return [
                {
                    "issueKey": "PROJ-1",
                    "startDate": "2026-04-02",
                    "timeSpentSeconds": 3600,
                }
            ]

    issues = [Issue(key="PROJ-1", issue_type="Bug", summary="Bug summary")]
    result = _existing_jira_worklogs_by_issue_day(
        TempoOnlyClient(),
        issues,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    assert result[("PROJ-1", date(2026, 4, 2))] == (60, None)


def test_existing_jira_worklogs_by_issue_day_does_not_double_count_jira_and_tempo() -> None:
    class JiraAndTempoClient(FakeClient):
        def get_tempo_worklogs_for_user(self, *, account_id: str, from_date: date, to_date: date) -> list[dict]:
            return [
                {
                    "issueKey": "PROJ-1",
                    "startDate": "2026-04-01",
                    "timeSpentSeconds": 7200,
                }
            ]

    issues = [Issue(key="PROJ-1", issue_type="Bug", summary="Bug summary")]
    result = _existing_jira_worklogs_by_issue_day(
        JiraAndTempoClient(),
        issues,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    # Jira already provides 150 min for this key/day, so Tempo should not add to it.
    assert result[("PROJ-1", date(2026, 4, 1))] == (150, 101)


def test_existing_jira_worklogs_by_issue_day_matches_author_key_when_no_account_id() -> None:
    class KeyIdentityClient(FakeClient):
        def get_current_user_account_id(self) -> str:
            return "my-key"

        def get_issue_worklogs(self, issue_key: str) -> list[dict]:
            if issue_key != "PROJ-1":
                return []
            return [
                {
                    "id": "201",
                    "author": {"key": "my-key"},
                    "started": "2026-04-01T09:00:00.000-0300",
                    "timeSpentSeconds": 3600,
                }
            ]

    issues = [Issue(key="PROJ-1", issue_type="Bug", summary="Bug summary")]
    result = _existing_jira_worklogs_by_issue_day(
        KeyIdentityClient(),
        issues,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    assert result[("PROJ-1", date(2026, 4, 1))] == (60, 201)


def test_existing_jira_worklogs_by_issue_day_matches_author_email_when_no_account_id() -> None:
    class EmailIdentityClient(FakeClient):
        def get_current_user_account_id(self) -> str:
            return "me@example.com"

        def get_issue_worklogs(self, issue_key: str) -> list[dict]:
            if issue_key != "PROJ-1":
                return []
            return [
                {
                    "id": "301",
                    "author": {"emailAddress": "me@example.com"},
                    "started": "2026-04-01T09:00:00.000-0300",
                    "timeSpentSeconds": 5400,
                }
            ]

    issues = [Issue(key="PROJ-1", issue_type="Bug", summary="Bug summary")]
    result = _existing_jira_worklogs_by_issue_day(
        EmailIdentityClient(),
        issues,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    assert result[("PROJ-1", date(2026, 4, 1))] == (90, 301)


def test_existing_jira_worklogs_by_issue_day_prefers_account_id_over_legacy_fields() -> None:
    class StrictAccountClient(FakeClient):
        def get_current_user_account_id(self) -> str:
            return "user-123"

        def get_issue_worklogs(self, issue_key: str) -> list[dict]:
            if issue_key != "PROJ-1":
                return []
            return [
                {
                    "id": "401",
                    "author": {"accountId": "other-user", "emailAddress": "user-123"},
                    "started": "2026-04-01T09:00:00.000-0300",
                    "timeSpentSeconds": 3600,
                }
            ]

    issues = [Issue(key="PROJ-1", issue_type="Bug", summary="Bug summary")]
    result = _existing_jira_worklogs_by_issue_day(
        StrictAccountClient(),
        issues,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    assert result == {}


def test_existing_jira_worklogs_by_issue_day_uses_legacy_fields_only_when_account_id_missing() -> None:
    class LegacyFallbackClient(FakeClient):
        def get_current_user_account_id(self) -> str:
            return "legacy-key"

        def get_issue_worklogs(self, issue_key: str) -> list[dict]:
            if issue_key != "PROJ-1":
                return []
            return [
                {
                    "id": "402",
                    "author": {"key": "legacy-key"},
                    "started": "2026-04-01T09:00:00.000-0300",
                    "timeSpentSeconds": 1800,
                }
            ]

    issues = [Issue(key="PROJ-1", issue_type="Bug", summary="Bug summary")]
    result = _existing_jira_worklogs_by_issue_day(
        LegacyFallbackClient(),
        issues,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    )

    assert result[("PROJ-1", date(2026, 4, 1))] == (30, 402)
