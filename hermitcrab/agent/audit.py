"""Small filesystem-native audit trail helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AuditSummary:
    path: str
    exists: bool
    event_count: int
    last_event: str | None
    last_timestamp: str | None


class AuditTrail:
    """Append-only JSONL audit trail for meaningful runtime events."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.path = workspace / "logs" / "audit.jsonl"

    def record(self, event: str, **data: Any) -> None:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **data,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def summarize(self) -> AuditSummary:
        if not self.path.exists():
            return AuditSummary(
                path=str(self.path),
                exists=False,
                event_count=0,
                last_event=None,
                last_timestamp=None,
            )

        count = 0
        last_event: str | None = None
        last_timestamp: str | None = None
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    count += 1
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        event = payload.get("event")
                        timestamp = payload.get("ts")
                        if isinstance(event, str) and event.strip():
                            last_event = event.strip()
                        if isinstance(timestamp, str) and timestamp.strip():
                            last_timestamp = timestamp.strip()
        except OSError:
            return AuditSummary(
                path=str(self.path),
                exists=False,
                event_count=0,
                last_event=None,
                last_timestamp=None,
            )

        return AuditSummary(
            path=str(self.path),
            exists=True,
            event_count=count,
            last_event=last_event,
            last_timestamp=last_timestamp,
        )

    def read_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent audit entries, oldest-to-newest within the returned window."""
        if limit <= 0 or not self.path.exists():
            return []

        entries: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        entries.append(payload)
        except OSError:
            return []

        return entries[-limit:]
