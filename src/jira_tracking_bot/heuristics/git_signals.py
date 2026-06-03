from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ISSUE_KEY_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


@dataclass(slots=True)
class Suggestion:
    minutes: int
    source: str
    confidence: str



def suggest_minutes_from_git(
    *, repo_path: Path, since: datetime, until: datetime, minutes_per_commit: int = 30
) -> dict[str, Suggestion]:
    if not repo_path.exists():
        return {}

    command = [
        "git",
        "-C",
        str(repo_path),
        "log",
        "--pretty=%s",
        "--since",
        since.isoformat(),
        "--until",
        until.isoformat(),
    ]

    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        return {}

    if result.returncode != 0:
        return {}

    counts: dict[str, int] = {}
    for subject in result.stdout.splitlines():
        matches = ISSUE_KEY_PATTERN.findall(subject)
        for key in matches:
            counts[key] = counts.get(key, 0) + 1

    suggestions: dict[str, Suggestion] = {}
    for key, count in counts.items():
        minutes = max(minutes_per_commit, count * minutes_per_commit)
        confidence = "high" if count >= 3 else "medium"
        suggestions[key] = Suggestion(minutes=minutes, source=f"git:{count} commits", confidence=confidence)

    return suggestions
