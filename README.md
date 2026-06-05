# Jira Tracking Web

Local CLI to discover Jira tickets you worked on and submit worklogs with explicit confirmation.

## Web App (Recommended)

The project now includes a visual month planner web app where you can edit per-day ticket minutes before submission.

### One-command local run (Docker)

Use one command to build and run frontend + API at http://localhost:8080:

```bash
docker compose up --build
```

The container runs:

- Nginx for the frontend (SPA)
- Uvicorn backend API (proxied as `/api`)
- Compose healthcheck against `/api/health`
- Restart policy `unless-stopped` for server-style operation

### Docker setup

1. Create and edit `.env`:

```bash
cp .env.example .env
```

2. Ensure these values are set in `.env`:

- `JIRA_TRACK_JIRA_BASE_URL`
- `JIRA_TRACK_TIMEZONE` (defaults to `America/New_York` if omitted)

3. Keep SSO session persisted on host via `.jira-track/session.json` (mounted into container).

### SSO login with minimal container footprint

To avoid running a full browser desktop stack inside Docker, use host Chrome via CDP:

1. Start Chrome with remote debugging enabled:

macOS:

```bash
open -na "Google Chrome" --args --remote-debugging-port=9222
```

Linux:

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/jira-track-chrome
```

Windows (PowerShell):

```powershell
Start-Process chrome.exe "--remote-debugging-port=9222 --user-data-dir=$env:TEMP\jira-track-chrome"
```

WSL/WSL2 (launches Windows Chrome from WSL):

```bash
cmd.exe /C start "" "chrome.exe" --remote-debugging-port=9222 --user-data-dir="%TEMP%\\jira-track-chrome"
```

2. Trigger login from the web app (or API) while Docker is running.

By default, compose sets `JIRA_TRACK_CDP_URL=http://host.docker.internal:9222`.

### Legacy local dev workflow (optional)

If you prefer running without Docker for development, use two terminals.

Start backend API:

```bash
source .venv/bin/activate
jira-track-web
```

Start frontend:

```bash
cd web
npm install
npm run dev
```

Open http://localhost:5173.

### Web workflow defaults

- Loads the current month (up to today)
- Creates weekday entries for in-progress tickets
- Rebalances each weekday to 8h (480 minutes)
- Never allows submit when a weekday total exceeds 8h
- Detects already-logged Tempo day+ticket worklogs for current user
- Allows per-entry minute edits and remove/restore before submit

### Export Logs modal

- The Export Logs modal renders each day as structured report state with `In Progress` and `Done` folders, per-line checkboxes, and draggable non-folder lines.
- Reusable export activities are stored in the browser's localStorage and are appended to each day's `Done` folder by default unless the user reorders them.
- Preview and Copy Day must always use the same shared text-output builder. Do not split these paths or duplicate formatting logic; any output change must update the shared builder so rendered preview and copied rich text stay identical.

## Features

- Finds your tickets for `day`, `week`, or `month`
- Suggests minutes per ticket from git commit messages containing issue keys (for example: `PROJ-1234 fix parser`)
- Shows a preview table before any write
- Interactive review: edit minutes per ticket before submit
- Mandatory confirmation gate before writing to Jira

## Requirements

- Python 3.11+
- Jira web access through SSO in your browser (session-based auth)

## Setup

1. Create virtual environment and install package:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
python3 -m playwright install chromium
```

2. Recommended: use project-local environment file (keeps setup simple and explicit):

```bash
cp .env.example .env
```

3. Edit `.env` with real values.

Alternative: create a JSON config file (project-local first, then home path fallback):

```bash
cp config.example.json .jira-track.json
```

You can also use `~/.jira-track.json` if you want one shared config across repositories.

You can override any config value with environment variables:

- `JIRA_TRACK_JIRA_BASE_URL`
- `JIRA_TRACK_TEMPO_API_TOKEN`
- `JIRA_TRACK_TEMPO_API_BASE` (defaults to `https://api.tempo.io/4`)
- `JIRA_TRACK_TIMEZONE` (defaults to `America/New_York`)
- `JIRA_TRACK_DEFAULT_PROJECTS` (comma-separated, for example `PROJ,OPS,PLAT`)
- `JIRA_TRACK_SESSION_STATE_PATH` (defaults to `.jira-track/session.json`)
- `JIRA_TRACK_CDP_URL` (optional, for host Chrome CDP; example `http://host.docker.internal:9222`)
- `JIRA_TRACK_CDP_ALLOWED_HOSTS` (comma-separated host allowlist, defaults to `127.0.0.1,localhost,host.docker.internal`)
- `JIRA_TRACK_CORS_ORIGINS` (comma-separated allowed web origins)
- `JIRA_TRACK_WEB_FORWARDED_ALLOW_IPS` (comma-separated trusted proxy IPs for forwarded headers, defaults to `127.0.0.1`)

Set `JIRA_TRACK_TIMEZONE` to your own IANA timezone in `.env`.

Timezone examples (first is default):

- `America/New_York` (USA Eastern)
- `America/Argentina/Buenos_Aires`
- `Asia/Kolkata` (India)

## Usage

Run tests with the project virtualenv (recommended, avoids global Python issues):

```bash
bash scripts/test.sh
```

Pass any pytest args through the same command:

```bash
bash scripts/test.sh tests/test_config.py -q
```

CLI commands remain available:

Validate auth:

```bash
jira-track auth-check
```

Log in through browser SSO and save the session locally:

```bash
jira-track login-sso
```

Reuse an already-running Chrome session (so you can often avoid re-entering credentials):

1. Start Chrome with remote debugging enabled:

macOS:

```bash
open -na "Google Chrome" --args --remote-debugging-port=9222
```

Linux:

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/jira-track-chrome
```

Windows (PowerShell):

```powershell
Start-Process chrome.exe "--remote-debugging-port=9222 --user-data-dir=$env:TEMP\jira-track-chrome"
```

WSL/WSL2 (launches Windows Chrome from WSL):

```bash
cmd.exe /C start "" "chrome.exe" --remote-debugging-port=9222 --user-data-dir="%TEMP%\\jira-track-chrome"
```

2. Run login using that browser session:

```bash
jira-track login-sso --cdp-url http://127.0.0.1:9222
```

Delete the saved SSO session:

```bash
jira-track logout-sso
```

Diagnose auth mode and endpoint reachability:

```bash
jira-track diagnose-auth
```

Preview weekly suggestions:

```bash
jira-track preview --period week --repo-path .
```

Review and submit worklogs:

```bash
jira-track submit --period week --repo-path . --comment "Weekly log"
```

## Notes

- The tool searches issues assigned to `currentUser()` and updated in the selected period.
- Suggested time is heuristic only; always review before confirming.
- Authentication is SSO-only and uses a saved Playwright browser session.
- If the browser can reach Jira but API requests fail, run `jira-track login-sso` again to refresh the saved session.
- The saved browser session is stored locally in `.jira-track/session.json` by default and should be treated like a credential.
