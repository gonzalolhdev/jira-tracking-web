from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from jira_tracking_bot.config import AppConfig


class SessionAuthError(RuntimeError):
    """Raised for SSO session storage errors."""



def login_with_browser_session(
    config: AppConfig,
    *,
    headless: bool = False,
    cdp_url: str | None = None,
) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SessionAuthError(
            "Playwright is not installed. Reinstall dependencies and run `python3 -m playwright install chromium`."
        ) from exc

    session_path = config.session_state_path
    session_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        if cdp_url:
            try:
                browser = playwright.chromium.connect_over_cdp(cdp_url)
            except Exception as exc:
                raise SessionAuthError(
                    "Could not connect to Chrome via CDP. Start Chrome with remote debugging enabled, "
                    "for example: open -na \"Google Chrome\" --args --remote-debugging-port=9222 "
                    "--user-data-dir=/tmp/jira-track-chrome"
                ) from exc
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(config.jira_base_url, wait_until="domcontentloaded")
            input("Complete/confirm SSO in your existing Chrome tab, then press Enter to save the session...")
            context.storage_state(path=str(session_path))
            browser.close()
        else:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context()
            page = context.new_page()
            page.goto(config.jira_base_url, wait_until="domcontentloaded")
            input("Complete SSO login in the opened browser, then press Enter here to save the session...")
            context.storage_state(path=str(session_path))
            browser.close()

    return session_path


def login_with_browser_session_web(
    config: AppConfig,
    *,
    timeout_seconds: int = 240,
    cdp_url: str | None = None,
) -> Path:
    """Open browser for SSO and persist session once Jira auth is detected."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SessionAuthError(
            "Playwright is not installed. Reinstall dependencies and run `python3 -m playwright install chromium`."
        ) from exc

    session_path = config.session_state_path
    session_path.parent.mkdir(parents=True, exist_ok=True)
    myself_url = f"{config.jira_base_url.rstrip('/')}/rest/api/2/myself"

    with sync_playwright() as playwright:
        remote_browser = bool(cdp_url)
        if cdp_url:
            try:
                browser = playwright.chromium.connect_over_cdp(cdp_url)
            except Exception as exc:
                raise SessionAuthError(
                    "Could not connect to Chrome via CDP. Start Chrome with remote debugging enabled, "
                    "for example: open -na \"Google Chrome\" --args --remote-debugging-port=9222"
                ) from exc
            context = browser.contexts[0] if browser.contexts else browser.new_context()
        else:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context()

        page = context.new_page()
        page.goto(config.jira_base_url, wait_until="domcontentloaded")

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                response = context.request.get(myself_url)
                if response.status == 200:
                    context.storage_state(path=str(session_path))
                    page.close()
                    if not remote_browser:
                        browser.close()
                    return session_path
            except Exception:
                pass
            page.wait_for_timeout(2000)

        page.close()
        if not remote_browser:
            browser.close()

    raise SessionAuthError(
        "Timed out waiting for SSO authentication in browser. Complete login and retry."
    )



def delete_browser_session(config: AppConfig) -> bool:
    if not config.session_state_path.exists():
        return False
    config.session_state_path.unlink()
    return True



def load_session_cookies(path: Path) -> httpx.Cookies:
    if not path.exists():
        raise SessionAuthError(
            f"SSO session state not found at {path}. Run `jira-track login-sso` first or switch auth mode."
        )

    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SessionAuthError(f"Invalid Playwright storage state in {path}: {exc}") from exc

    cookies = httpx.Cookies()
    for cookie in payload.get("cookies", []):
        name = str(cookie.get("name", "")).strip()
        value = str(cookie.get("value", ""))
        domain = str(cookie.get("domain", "")).strip() or None
        cookie_path = str(cookie.get("path", "/") or "/")
        if name:
            cookies.set(name, value, domain=domain, path=cookie_path)

    return cookies



def session_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "cookies": 0, "origins": 0}

    payload = json.loads(path.read_text())
    return {
        "exists": True,
        "cookies": len(payload.get("cookies", [])),
        "origins": len(payload.get("origins", [])),
    }


def request_with_browser_session(
    *,
    session_state_path: Path,
    base_url: str,
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    timeout_seconds: int = 20,
) -> tuple[int, str, str]:
    """Run an HTTP request from a browser context using saved SSO session state."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SessionAuthError(
            "Playwright is not installed. Reinstall dependencies and run `python3 -m playwright install chromium`."
        ) from exc

    if not session_state_path.exists():
        raise SessionAuthError(
            f"SSO session state not found at {session_state_path}. Run `jira-track login-sso` first."
        )

    endpoint = f"{base_url.rstrip('/')}{path}"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(session_state_path))
        context.set_default_timeout(timeout_seconds * 1000)
        try:
            headers = {"Accept": "application/json"}
            method_upper = method.upper()
            # Jira Data Center often enforces XSRF checks for cookie-based non-GET requests.
            # The REST API supports this opt-out header for script/API clients.
            if method_upper not in {"GET", "HEAD", "OPTIONS"}:
                headers["X-Atlassian-Token"] = "no-check"
                headers["X-Requested-With"] = "XMLHttpRequest"
            data = None
            if json_body is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(json_body)

            if method_upper in {"GET", "HEAD", "OPTIONS"}:
                response = context.request.fetch(
                    endpoint,
                    method=method_upper,
                    headers=headers,
                    data=data,
                    timeout=timeout_seconds * 1000,
                )
                # Read body while Playwright is still running — text() is async internally
                status = int(response.status)
                content_type = str(response.headers.get("content-type", ""))
                body = str(response.text())
            else:
                # Execute write requests from an actual Jira page origin to satisfy strict XSRF filters.
                page = context.new_page()
                page.goto(base_url, wait_until="domcontentloaded")
                result = page.evaluate(
                    """
                    async ({ endpoint, method, headers, data }) => {
                      const response = await fetch(endpoint, {
                        method,
                        headers,
                        body: data,
                        credentials: "include",
                      });
                      const text = await response.text();
                      return {
                        status: response.status,
                        contentType: response.headers.get("content-type") || "",
                        body: text,
                      };
                    }
                    """,
                    {
                        "endpoint": endpoint,
                        "method": method_upper,
                        "headers": headers,
                        "data": data,
                    },
                )
                page.close()
                status = int(result.get("status", 0))
                content_type = str(result.get("contentType", ""))
                body = str(result.get("body", ""))
        except SessionAuthError:
            raise
        except Exception as exc:
            raise SessionAuthError(
                "Browser-session request failed or timed out. Your SSO session may be expired, or this origin blocks automated browser requests. "
                "Run `jira-track login-sso` again and retry."
            ) from exc
        finally:
            browser.close()

    return status, content_type, body
