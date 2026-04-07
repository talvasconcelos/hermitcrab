"""Filesystem-native people profile artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter

from hermitcrab.utils.helpers import ensure_dir, journal_day_wikilink, safe_filename


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class PersonProfile:
    """One person/profile artifact."""

    file_path: Path
    name: str
    role: str
    status: str
    timezone: str = ""
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, file_path: Path) -> PersonProfile | None:
        try:
            post = frontmatter.load(file_path)
        except Exception:
            return None
        meta = post.metadata or {}
        return cls(
            file_path=file_path,
            name=str(meta.get("name") or meta.get("title") or file_path.stem),
            role=str(meta.get("role") or "member"),
            status=str(meta.get("status") or "active"),
            timezone=str(meta.get("timezone") or ""),
            aliases=[str(item).strip() for item in meta.get("aliases", []) if str(item).strip()],
            tags=[str(item).strip() for item in meta.get("tags", []) if str(item).strip()],
            notes=(post.content or "").strip(),
            created_at=str(meta.get("created_at") or ""),
            updated_at=str(meta.get("updated_at") or ""),
            metadata=meta,
        )

    def to_post(self) -> frontmatter.Post:
        post = frontmatter.Post(self.notes)
        post.metadata.update(
            {
                "title": self.name,
                "name": self.name,
                "type": "person_profile",
                "role": self.role,
                "status": self.status,
                "aliases": self.aliases,
                "tags": self.tags,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "journal": journal_day_wikilink(self.created_at or self.updated_at),
            }
        )
        if self.timezone:
            post.metadata["timezone"] = self.timezone
        return post


class PeopleStore:
    """Manage simple people profile artifacts."""

    VALID_ROLES = {"owner", "family", "child", "member", "guest", "contact", "client", "collaborator"}
    VALID_STATUSES = {"active", "inactive"}

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.people_dir = ensure_dir(workspace / "knowledge" / "notes" / "people")
        self.profiles_dir = ensure_dir(self.people_dir / "profiles")

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.casefold().split())

    def _path_for_name(self, name: str) -> Path:
        slug = safe_filename(name).strip().lower().replace(" ", "-")
        return self.profiles_dir / f"{slug}.md"

    def get_profile(self, query: str) -> PersonProfile | None:
        normalized = self._normalize(query)
        exact: PersonProfile | None = None
        partials: list[PersonProfile] = []
        for file_path in sorted(self.profiles_dir.glob("*.md")):
            item = PersonProfile.from_file(file_path)
            if item is None:
                continue
            names = {
                self._normalize(item.name),
                self._normalize(file_path.stem.replace("-", " ")),
                *(self._normalize(alias) for alias in item.aliases),
            }
            if normalized in names:
                exact = item
                break
            if normalized and any(normalized in candidate for candidate in names):
                partials.append(item)
        return exact or (partials[0] if len(partials) == 1 else None)

    def list_profiles(self, include_inactive: bool = False) -> list[PersonProfile]:
        items: list[PersonProfile] = []
        for file_path in sorted(self.profiles_dir.glob("*.md")):
            item = PersonProfile.from_file(file_path)
            if item is None:
                continue
            if not include_inactive and item.status != "active":
                continue
            items.append(item)
        return sorted(items, key=lambda item: (item.status != "active", item.name.casefold()))

    def upsert_profile(
        self,
        *,
        name: str,
        role: str,
        status: str = "active",
        timezone: str | None = None,
        aliases: list[str] | None = None,
        tags: list[str] | None = None,
        notes: str | None = None,
        existing_query: str | None = None,
    ) -> PersonProfile:
        role = role.strip().lower()
        status = status.strip().lower()
        if role not in self.VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(self.VALID_ROLES))}")
        if status not in self.VALID_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(self.VALID_STATUSES))}")

        existing = self.get_profile(existing_query or name)
        now = _utcnow().isoformat()
        item = PersonProfile(
            file_path=(existing.file_path if existing else self._path_for_name(name)),
            name=name.strip(),
            role=role,
            status=status,
            timezone=(timezone or "").strip(),
            aliases=[str(item).strip() for item in (aliases or []) if str(item).strip()],
            tags=[str(item).strip() for item in (tags or []) if str(item).strip()],
            notes=(notes or "").strip(),
            created_at=(existing.created_at if existing and existing.created_at else now),
            updated_at=now,
        )
        item.file_path.write_text(frontmatter.dumps(item.to_post()), encoding="utf-8")
        return item

    def deactivate_profile(self, query: str) -> PersonProfile | None:
        item = self.get_profile(query)
        if item is None:
            return None
        item.status = "inactive"
        item.updated_at = _utcnow().isoformat()
        item.file_path.write_text(frontmatter.dumps(item.to_post()), encoding="utf-8")
        return item

    def render_summary(self, item: PersonProfile) -> str:
        suffix = f", tz={item.timezone}" if item.timezone else ""
        return f"- {item.name} [{item.role}, {item.status}{suffix}]"
