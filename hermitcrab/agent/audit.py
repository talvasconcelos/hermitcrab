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

    def __init__(
        self,
        workspace: Path,
        *,
        max_bytes: int = 256 * 1024,
        max_archives: int = 5,
    ):
        self.workspace = workspace
        self.path = workspace / "logs" / "audit.jsonl"
        self.archive_dir = self.path.parent / "archive"
        self.max_bytes = max_bytes
        self.max_archives = max_archives

    def record(self, event: str, **data: Any) -> None:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **data,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        self._rotate_if_needed(len(encoded.encode("utf-8")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)

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

    def _rotate_if_needed(self, incoming_size: int) -> None:
        if self.max_bytes <= 0 or not self.path.exists():
            return
        try:
            current_size = self.path.stat().st_size
        except OSError:
            return
        if current_size + incoming_size <= self.max_bytes:
            return

        self.archive_dir.mkdir(parents=True, exist_ok=True)
        archive_name = f"audit-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.jsonl"
        archive_path = self.archive_dir / archive_name
        self.path.replace(archive_path)
        self._prune_archives()

    def _prune_archives(self) -> None:
        if self.max_archives < 0 or not self.archive_dir.exists():
            return
        archives = sorted(self.archive_dir.glob("audit-*.jsonl"))
        excess = len(archives) - self.max_archives
        if excess <= 0:
            return
        for archive_path in archives[:excess]:
            try:
                archive_path.unlink()
            except OSError:
                continue
