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
    is_primary: bool = False
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
            is_primary=bool(meta.get("is_primary", False)),
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
                "is_primary": self.is_primary,
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


@dataclass(slots=True)
class PersonInteraction:
    """One interaction note linked to a person profile."""

    file_path: Path
    person_name: str
    summary: str
    occurred_at: str
    channel: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, file_path: Path) -> PersonInteraction | None:
        try:
            post = frontmatter.load(file_path)
        except Exception:
            return None
        meta = post.metadata or {}
        return cls(
            file_path=file_path,
            person_name=str(meta.get("person_name") or ""),
            summary=(post.content or "").strip(),
            occurred_at=str(meta.get("occurred_at") or meta.get("created_at") or ""),
            channel=str(meta.get("channel") or ""),
            tags=[str(item).strip() for item in meta.get("tags", []) if str(item).strip()],
            created_at=str(meta.get("created_at") or ""),
            metadata=meta,
        )

    def to_post(self) -> frontmatter.Post:
        post = frontmatter.Post(self.summary)
        post.metadata.update(
            {
                "title": f"Interaction with {self.person_name}",
                "type": "person_interaction",
                "person_name": self.person_name,
                "occurred_at": self.occurred_at,
                "created_at": self.created_at,
                "journal": journal_day_wikilink(self.occurred_at or self.created_at),
            }
        )
        if self.channel:
            post.metadata["channel"] = self.channel
        if self.tags:
            post.metadata["tags"] = self.tags
        return post


class PeopleStore:
    """Manage simple people profile artifacts."""

    VALID_ROLES = {"owner", "family", "child", "member", "guest", "contact", "client", "collaborator"}
    VALID_STATUSES = {"active", "inactive"}

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.people_dir = ensure_dir(workspace / "knowledge" / "notes" / "people")
        self.profiles_dir = ensure_dir(self.people_dir / "profiles")
        self.interactions_dir = ensure_dir(self.people_dir / "interactions")

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.casefold().split())

    def _path_for_name(self, name: str) -> Path:
        slug = safe_filename(name).strip().lower().replace(" ", "-")
        return self.profiles_dir / f"{slug}.md"

    def _profile_names(self, item: PersonProfile) -> set[str]:
        return {
            self._normalize(item.name),
            self._normalize(item.file_path.stem.replace("-", " ")),
            *(self._normalize(alias) for alias in item.aliases),
        }

    def _interaction_path(self, person_name: str, occurred_at: str) -> Path:
        stamp = safe_filename(occurred_at.replace(":", "-")).strip().lower()
        slug = safe_filename(person_name).strip().lower().replace(" ", "-")
        return self.interactions_dir / f"{stamp}--{slug}.md"

    def get_profile(self, query: str) -> PersonProfile | None:
        normalized = self._normalize(query)
        exact: PersonProfile | None = None
        partials: list[PersonProfile] = []
        for file_path in sorted(self.profiles_dir.glob("*.md")):
            item = PersonProfile.from_file(file_path)
            if item is None:
                continue
            names = self._profile_names(item)
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

    def get_primary_profile(self) -> PersonProfile | None:
        for item in self.list_profiles(include_inactive=True):
            if item.is_primary:
                return item
        return None

    def find_duplicate_candidates(
        self,
        *,
        name: str,
        aliases: list[str] | None = None,
        exclude_path: Path | None = None,
    ) -> list[PersonProfile]:
        requested_names = {
            self._normalize(name),
            *(self._normalize(alias) for alias in (aliases or [])),
        }
        requested_names.discard("")
        matches: list[PersonProfile] = []
        for item in self.list_profiles(include_inactive=True):
            if exclude_path is not None and item.file_path == exclude_path:
                continue
            if requested_names & self._profile_names(item):
                matches.append(item)
        return sorted(matches, key=lambda item: item.name.casefold())

    def _write_profile(self, item: PersonProfile) -> None:
        item.file_path.write_text(frontmatter.dumps(item.to_post()), encoding="utf-8")

    def _set_primary(self, item: PersonProfile) -> None:
        current = self.get_primary_profile()
        if current and current.file_path != item.file_path:
            current.is_primary = False
            current.updated_at = _utcnow().isoformat()
            self._write_profile(current)
        item.is_primary = True

    def upsert_profile(
        self,
        *,
        name: str,
        role: str,
        status: str = "active",
        timezone: str | None = None,
        make_primary: bool | None = None,
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
        duplicate_candidates = self.find_duplicate_candidates(
            name=name,
            aliases=aliases,
            exclude_path=(existing.file_path if existing else None),
        )
        if existing is None and duplicate_candidates:
            suggestions = ", ".join(item.name for item in duplicate_candidates[:3])
            raise ValueError(
                "possible duplicate profile matches existing people: "
                f"{suggestions}. Use update/set-primary if you meant an existing profile."
            )
        now = _utcnow().isoformat()
        item = PersonProfile(
            file_path=(existing.file_path if existing else self._path_for_name(name)),
            name=name.strip(),
            role=role,
            status=status,
            timezone=(timezone or "").strip(),
            is_primary=(existing.is_primary if existing else False),
            aliases=[str(item).strip() for item in (aliases or []) if str(item).strip()],
            tags=[str(item).strip() for item in (tags or []) if str(item).strip()],
            notes=(notes or "").strip(),
            created_at=(existing.created_at if existing and existing.created_at else now),
            updated_at=now,
        )
        if make_primary is True or (role == "owner" and self.get_primary_profile() is None):
            self._set_primary(item)
        elif make_primary is False:
            item.is_primary = False
        self._write_profile(item)
        return item

    def deactivate_profile(self, query: str) -> PersonProfile | None:
        item = self.get_profile(query)
        if item is None:
            return None
        item.status = "inactive"
        item.is_primary = False
        item.updated_at = _utcnow().isoformat()
        self._write_profile(item)
        return item

    def set_primary_profile(self, query: str) -> PersonProfile | None:
        item = self.get_profile(query)
        if item is None:
            return None
        item.updated_at = _utcnow().isoformat()
        self._set_primary(item)
        self._write_profile(item)
        return item

    def add_interaction(
        self,
        *,
        query: str,
        summary: str,
        occurred_at: str | None = None,
        channel: str | None = None,
        tags: list[str] | None = None,
    ) -> tuple[PersonProfile, PersonInteraction]:
        person = self.get_profile(query)
        if person is None:
            raise ValueError(f"People profile not found: {query}")
        occurred = (occurred_at or _utcnow().isoformat()).strip()
        interaction = PersonInteraction(
            file_path=self._interaction_path(person.name, occurred),
            person_name=person.name,
            summary=summary.strip(),
            occurred_at=occurred,
            channel=(channel or "").strip(),
            tags=[str(item).strip() for item in (tags or []) if str(item).strip()],
            created_at=_utcnow().isoformat(),
        )
        interaction.file_path.write_text(frontmatter.dumps(interaction.to_post()), encoding="utf-8")
        return person, interaction

    def list_interactions(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> tuple[PersonProfile | None, list[PersonInteraction]]:
        person = self.get_profile(query)
        if person is None:
            return None, []
        items: list[PersonInteraction] = []
        normalized = self._normalize(person.name)
        for file_path in sorted(self.interactions_dir.glob("*.md"), reverse=True):
            interaction = PersonInteraction.from_file(file_path)
            if interaction is None:
                continue
            if self._normalize(interaction.person_name) != normalized:
                continue
            items.append(interaction)
            if len(items) >= max(1, limit):
                break
        return person, items

    def render_summary(self, item: PersonProfile) -> str:
        suffix = f", tz={item.timezone}" if item.timezone else ""
        primary = ", primary" if item.is_primary else ""
        return f"- {item.name} [{item.role}, {item.status}{primary}{suffix}]"

    def render_interaction_summary(self, item: PersonInteraction) -> str:
        channel = f" via {item.channel}" if item.channel else ""
        return f"- {item.occurred_at}{channel}: {item.summary}"
