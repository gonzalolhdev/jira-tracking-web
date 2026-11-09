# Time Tracker

Personal tool for turning Jira activity into time entries (Tempo/Productive) through a monthly planner.

## Language

**Hot launch**:
Opening the app by resuming the container as it was left by the last cold launch; nothing is rebuilt, so code and configuration changes are not picked up.
_Avoid_: Restart, resume

**Cold launch**:
Rebuilding the image and recreating the container from the current code and configuration; the only way to apply changes.
_Avoid_: Rebuild, reinstall

**Stop**:
Halting the container without deleting it, so a hot launch can bring it back.
_Avoid_: Down, shutdown

**Header**:
The sticky bar at the top of every page: logo, navigation between pages, and the month picker. On the Jira page it also holds Submit Month.
_Avoid_: Topbar, dashboard

**Jira page**:
The month planner, where days and ticket entries are reviewed and submitted.

**Productive page**:
The logged-records report, where each day's export is prepared and sent to Productive.
_Avoid_: Export modal
