from __future__ import annotations

from fastapi.testclient import TestClient

from jira_tracking_bot.config import AppConfig
from jira_tracking_bot.productive_client import ProductiveClient, ProductiveTimeEntry
from jira_tracking_bot.web import app as web_app


class _FakeProductiveClient:
    def __init__(self, entries: list[ProductiveTimeEntry] | None = None) -> None:
        self.entries = entries or []
        self.created_note: str | None = None
        self.deleted_entry_ids: list[str] = []

    def get_time_entries(self, after: str, before: str) -> list[ProductiveTimeEntry]:
        _ = (after, before)
        return self.entries

    def get_my_person_id(self) -> str:
        return "person-1"

    def get_service_id_for_date(self, person_id: str, target_date: str) -> str | None:
        _ = (person_id, target_date)
        return "service-1"

    def create_time_entry(
        self,
        person_id: str,
        service_id: str,
        entry_date: str,
        note: str,
        time_minutes: int = 480,
    ) -> ProductiveTimeEntry:
        _ = (person_id, service_id, entry_date, time_minutes)
        self.created_note = note
        return ProductiveTimeEntry(
            id="entry-1",
            date=entry_date,
            time=time_minutes,
            note=note,
            service_id=service_id,
        )

    def delete_time_entry(self, entry_id: str) -> None:
        self.deleted_entry_ids.append(entry_id)


def _fake_config() -> AppConfig:
    return AppConfig(
        jira_base_url="https://jira.example.com",
        jira_email="user@example.com",
        jira_token="token-123",
        timezone="UTC",
    )


def test_productive_entries_are_sanitized_before_return(monkeypatch) -> None:
    client = _FakeProductiveClient(
        entries=[
            ProductiveTimeEntry(
                id="entry-1",
                date="2026-09-18",
                time=480,
                note=(
                    '<div onclick="alert(1)">'
                    '<script>alert(2)</script>'
                    '<p>Hello & welcome <a href="javascript:alert(3)" target="_blank" onclick="bad()">there</a></p>'
                    '</div>'
                ),
                service_id="service-1",
            )
        ]
    )

    monkeypatch.setattr(web_app, "load_config", lambda require_auth=False: _fake_config())
    monkeypatch.setattr(web_app, "_get_productive_client", lambda: client)

    resp = TestClient(web_app.app).get("/api/productive/time-entries")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["available"] is True
    assert payload["entries"][0]["note"] == (
        '<div><p>Hello &amp; welcome <a target="_blank" rel="noreferrer noopener">there</a></p></div>'
    )


def test_productive_entry_creation_sanitizes_payload_before_storage(monkeypatch) -> None:
    client = _FakeProductiveClient()

    monkeypatch.setattr(web_app, "_get_productive_client", lambda: client)

    resp = TestClient(web_app.app).post(
        "/api/productive/time-entries",
        json={
            "date": "2026-09-18",
            "html": (
                '<p onclick="evil()">Daily update <img src="x" onerror="alert(1)"></p>'
                '<p><a href="https://example.com" target="_blank" onclick="bad()">Link</a></p>'
            ),
            "time_minutes": 480,
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert client.created_note == (
        '<p>Daily update </p><p><a href="https://example.com" target="_blank" rel="noreferrer noopener">Link</a></p>'
    )
    assert payload["note"] == client.created_note


def test_productive_entry_delete_endpoint_removes_existing_entry(monkeypatch) -> None:
    client = _FakeProductiveClient(
        entries=[
            ProductiveTimeEntry(
                id="entry-old",
                date="2026-09-18",
                time=480,
                note="old note",
                service_id="service-1",
            )
        ]
    )

    monkeypatch.setattr(web_app, "_get_productive_client", lambda: client)

    resp = TestClient(web_app.app).delete("/api/productive/time-entries/entry-old")

    assert resp.status_code == 204
    assert client.deleted_entry_ids == ["entry-old"]


def test_productive_delete_allows_empty_204_response(monkeypatch) -> None:
    client = ProductiveClient(base_url="https://productive.example.com", token="token", org_id="org-1")

    class _EmptyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b""

    def fake_urlopen(_req, *args, **kwargs):
        _ = (args, kwargs)
        return _EmptyResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client.delete_time_entry("entry-old")
