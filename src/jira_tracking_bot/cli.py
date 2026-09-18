from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from jira_tracking_bot.config import ConfigError, load_config
from jira_tracking_bot.discovery import build_jql_for_window, build_period_window
from jira_tracking_bot.heuristics.git_signals import suggest_minutes_from_git
from jira_tracking_bot.jira_client import JiraClient, JiraClientError
from jira_tracking_bot.review_flow import ask_for_confirmation, collect_final_minutes
from jira_tracking_bot.review_table import render_preview_table
from jira_tracking_bot.worklog_submitter import submit_worklogs

app = typer.Typer(help="Track worked Jira tickets and submit worklogs with preview confirmation")
console = Console()



def _load_client(config_path: str | None) -> tuple[JiraClient, object]:
    config = load_config(config_path)
    return JiraClient(config), config


@app.command("auth-check")
def auth_check(config: str | None = typer.Option(None, help="Path to config json")) -> None:
    """Validate Jira credentials."""
    try:
        client, _ = _load_client(config)
    except ConfigError as exc:
        raise typer.Exit(code=_print_error(str(exc)))

    try:
        client.validate_auth()
        console.print("Jira authentication looks good")
    except JiraClientError as exc:
        raise typer.Exit(code=_print_error(str(exc)))
    finally:
        client.close()


@app.command("diagnose-auth")
def diagnose_auth(config: str | None = typer.Option(None, help="Path to config json")) -> None:
    """Show resolved auth mode and probe Jira endpoints."""
    try:
        app_config = load_config(config, require_auth=False)
    except ConfigError as exc:
        raise typer.Exit(code=_print_error(str(exc)))

    table = Table(title="Jira Auth Diagnosis")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Base URL", f"{app_config.jira_base_url}/rest/api/3/")
    table.add_row("Auth mode", "api-token")
    table.add_row("Jira email configured", "yes" if app_config.jira_email else "no")
    table.add_row("Jira token configured", "yes" if app_config.jira_token else "no")

    try:
        client = JiraClient(app_config)
    except JiraClientError as exc:
        table.add_row("Client initialization", str(exc))
        console.print(table)
        raise typer.Exit(code=0)

    for label, path, authenticated in [
        ("Unauthenticated serverInfo", "/serverInfo", False),
        ("Authenticated myself", "/myself", True),
    ]:
        status, content_type, body = client.probe(path, authenticated=authenticated)
        value = f"HTTP {status} | {content_type or 'unknown'} | {body or '<empty>'}"
        table.add_row(label, value)

    console.print(table)
    client.close()


@app.command("preview")
def preview(
    period: str = typer.Option("week", help="day|week|month"),
    config: str | None = typer.Option(None, help="Path to config json"),
    repo_path: str = typer.Option(".", help="Git repo path for commit heuristics"),
) -> None:
    """Fetch issues and show preview table with suggested time."""
    try:
        client, app_config = _load_client(config)
    except ConfigError as exc:
        raise typer.Exit(code=_print_error(str(exc)))

    now = datetime.now(app_config.tzinfo)
    start, end = build_period_window(period, now)
    jql = build_jql_for_window(app_config, start, end)

    try:
        issues = client.search_issues(jql=jql)
    except JiraClientError as exc:
        client.close()
        raise typer.Exit(code=_print_error(str(exc)))

    suggestions = suggest_minutes_from_git(repo_path=Path(repo_path), since=start, until=end)
    suggested_minutes = {issue.key: suggestions.get(issue.key).minutes if issue.key in suggestions else 0 for issue in issues}
    sources = {issue.key: suggestions[issue.key].source for issue in issues if issue.key in suggestions}

    render_preview_table(
        issues=issues,
        suggested_minutes=suggested_minutes,
        final_minutes=suggested_minutes,
        sources=sources,
    )

    console.print(f"JQL used: {jql}")
    console.print(f"Found {len(issues)} issues")
    client.close()


@app.command("submit")
def submit(
    period: str = typer.Option("week", help="day|week|month"),
    config: str | None = typer.Option(None, help="Path to config json"),
    repo_path: str = typer.Option(".", help="Git repo path for commit heuristics"),
    comment: str | None = typer.Option(None, help="Worklog comment for submitted entries"),
) -> None:
    """Interactive review then submit worklogs."""
    try:
        client, app_config = _load_client(config)
    except ConfigError as exc:
        raise typer.Exit(code=_print_error(str(exc)))

    now = datetime.now(app_config.tzinfo)
    start, end = build_period_window(period, now)
    jql = build_jql_for_window(app_config, start, end)

    try:
        issues = client.search_issues(jql=jql)
    except JiraClientError as exc:
        client.close()
        raise typer.Exit(code=_print_error(str(exc)))

    suggestions = suggest_minutes_from_git(repo_path=Path(repo_path), since=start, until=end)
    suggested_minutes = {issue.key: suggestions.get(issue.key).minutes if issue.key in suggestions else 0 for issue in issues}
    sources = {issue.key: suggestions[issue.key].source for issue in issues if issue.key in suggestions}

    render_preview_table(
        issues=issues,
        suggested_minutes=suggested_minutes,
        final_minutes=suggested_minutes,
        sources=sources,
    )

    final_minutes = collect_final_minutes(issues, suggested_minutes)
    render_preview_table(
        issues=issues,
        suggested_minutes=suggested_minutes,
        final_minutes=final_minutes,
        sources=sources,
    )

    total_minutes = sum(final_minutes.values())
    if not ask_for_confirmation(total_minutes=total_minutes, period=period):
        console.print("Submission cancelled")
        client.close()
        raise typer.Exit(code=0)

    results = submit_worklogs(
        client=client,
        issues=issues,
        minutes_by_issue=final_minutes,
        started_at=now,
        comment=comment,
    )
    _print_results(results)
    client.close()



def _print_results(results: list) -> None:
    table = Table(title="Submission Results")
    table.add_column("Ticket")
    table.add_column("Minutes")
    table.add_column("Status")
    table.add_column("Message")

    for result in results:
        status = "ok" if result.success else "error"
        table.add_row(result.issue_key, str(result.minutes), status, result.message)

    console.print(table)



def _print_error(message: str) -> int:
    console.print(f"[red]{message}[/red]")
    return 1


if __name__ == "__main__":
    app()
