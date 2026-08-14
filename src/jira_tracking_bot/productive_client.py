from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode

import urllib.request
import json

logger = logging.getLogger(__name__)


class ProductiveClientError(RuntimeError):
    """Raised when the Productive API returns an error or is unreachable."""


@dataclass(slots=True)
class ProductiveTimeEntry:
    id: str
    date: str  # YYYY-MM-DD
    time: int  # minutes
    note: str
    service_id: str | None = None


class ProductiveClient:
    def __init__(self, base_url: str, token: str, org_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "X-Auth-Token": token,
            "X-Organization-Id": str(org_id),
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/vnd.api+json",
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                parsed = json.loads(raw)
            except Exception:
                raise ProductiveClientError(f"Productive API {method} {path} failed: HTTP {exc.code}") from exc
            errors = parsed.get("errors", [])
            detail = "; ".join(e.get("detail", e.get("title", "unknown")) for e in errors) if errors else raw
            raise ProductiveClientError(f"Productive API error: {detail}") from exc
        except Exception as exc:
            raise ProductiveClientError(f"Productive API request failed: {exc}") from exc

        try:
            return json.loads(raw)
        except Exception as exc:
            raise ProductiveClientError(f"Productive API returned non-JSON response") from exc

    def get_my_person_id(self) -> str | None:
        """Return the person ID of the authenticated user via the most recent time entry."""
        try:
            result = self._request("GET", "/time_entries", params={"page[size]": "1", "include": "person"})
        except ProductiveClientError:
            return None
        data = result.get("data", [])
        if not data:
            return None
        person_rel = data[0].get("relationships", {}).get("person", {})
        person_data = person_rel.get("data")
        if person_data and isinstance(person_data, dict):
            return person_data.get("id")
        included = result.get("included", [])
        for item in included:
            if item.get("type") == "people":
                return item.get("id")
        return None

    def get_time_entries(self, after: str, before: str) -> list[ProductiveTimeEntry]:
        """Fetch all time entries in the [after, before] date range (inclusive)."""
        params = {
            "filter[after]": after,
            "filter[before]": before,
            "page[size]": "200",
            "page[number]": "1",
            "include": "service",
        }
        try:
            result = self._request("GET", "/time_entries", params=params)
        except ProductiveClientError as exc:
            logger.warning("productive_get_time_entries_failed: %s", exc)
            return []

        # Build a map of included services for quick lookup
        included_services: dict[str, str] = {}
        for item in result.get("included", []):
            if item.get("type") == "services":
                included_services[item["id"]] = item.get("attributes", {}).get("name", "")

        entries: list[ProductiveTimeEntry] = []
        for item in result.get("data", []):
            attrs = item.get("attributes", {})
            service_id: str | None = None
            svc_rel = item.get("relationships", {}).get("service", {})
            svc_data = svc_rel.get("data")
            if svc_data and isinstance(svc_data, dict):
                service_id = svc_data.get("id")
            entries.append(ProductiveTimeEntry(
                id=item["id"],
                date=attrs.get("date", ""),
                time=attrs.get("time", 0),
                note=attrs.get("note") or "",
                service_id=service_id,
            ))
        return entries

    def get_service_id_for_date(self, person_id: str, target_date: str) -> str | None:
        """Return the service_id from a booking that covers the target date.

        Falls back to the service used in the most recent time entry if no booking
        is found.
        """
        # 1. Try "Scheduled On" services via bookings
        service_id = self._service_from_bookings(person_id, target_date)
        if service_id:
            return service_id

        # 2. Fall back to the service used in the most recent time entry
        return self._service_from_recent_entries()

    def _service_from_bookings(self, person_id: str, target_date: str) -> str | None:
        # Query bookings covering the target month (start of month to end of month)
        # Since bookings span a range, we query by month and filter in code.
        try:
            d = __import__("datetime").date.fromisoformat(target_date)
        except Exception:
            return None

        month_start = d.replace(day=1).isoformat()
        # Last day of month
        import calendar as _cal
        last_day = _cal.monthrange(d.year, d.month)[1]
        month_end = d.replace(day=last_day).isoformat()

        params = {
            "filter[person_id]": person_id,
            "filter[after]": month_start,
            "filter[before]": month_end,
            "include": "service",
            "page[size]": "20",
        }
        try:
            result = self._request("GET", "/bookings", params=params)
        except ProductiveClientError as exc:
            logger.warning("productive_get_bookings_failed: %s", exc)
            return None

        # Find a booking whose date range covers target_date
        for item in result.get("data", []):
            attrs = item.get("attributes", {})
            started = attrs.get("started_on", "")
            ended = attrs.get("ended_on", "")
            if not started or not ended:
                continue
            if started <= target_date <= ended:
                svc_rel = item.get("relationships", {}).get("service", {})
                svc_data = svc_rel.get("data")
                if svc_data and isinstance(svc_data, dict):
                    return svc_data.get("id")

        return None

    def _service_from_recent_entries(self) -> str | None:
        try:
            result = self._request("GET", "/time_entries", params={"page[size]": "1", "include": "service"})
        except ProductiveClientError:
            return None
        data = result.get("data", [])
        if not data:
            return None
        svc_rel = data[0].get("relationships", {}).get("service", {})
        svc_data = svc_rel.get("data")
        if svc_data and isinstance(svc_data, dict):
            return svc_data.get("id")
        return None

    def create_time_entry(
        self,
        person_id: str,
        service_id: str,
        entry_date: str,
        note: str,
        time_minutes: int = 480,
    ) -> ProductiveTimeEntry:
        """Create a new time entry in Productive."""
        body: dict[str, Any] = {
            "data": {
                "type": "time_entries",
                "attributes": {
                    "date": entry_date,
                    "note": note,
                    "time": time_minutes,
                },
                "relationships": {
                    "person": {"data": {"type": "people", "id": person_id}},
                    "service": {"data": {"type": "services", "id": service_id}},
                },
            }
        }
        result = self._request("POST", "/time_entries", body=body)
        item = result.get("data", {})
        attrs = item.get("attributes", {})
        svc_rel = item.get("relationships", {}).get("service", {})
        svc_data = svc_rel.get("data")
        return ProductiveTimeEntry(
            id=item["id"],
            date=attrs.get("date", entry_date),
            time=attrs.get("time", time_minutes),
            note=attrs.get("note") or "",
            service_id=svc_data.get("id") if svc_data else service_id,
        )

    def update_time_entry(
        self,
        entry_id: str,
        note: str,
        time_minutes: int = 480,
    ) -> ProductiveTimeEntry:
        """Update note/time of an existing time entry."""
        body: dict[str, Any] = {
            "data": {
                "type": "time_entries",
                "id": entry_id,
                "attributes": {
                    "note": note,
                    "time": time_minutes,
                },
            }
        }
        result = self._request("PATCH", f"/time_entries/{entry_id}", body=body)
        item = result.get("data", {})
        attrs = item.get("attributes", {})
        svc_rel = item.get("relationships", {}).get("service", {})
        svc_data = svc_rel.get("data")
        return ProductiveTimeEntry(
            id=item["id"],
            date=attrs.get("date", ""),
            time=attrs.get("time", time_minutes),
            note=attrs.get("note") or "",
            service_id=svc_data.get("id") if svc_data else None,
        )
