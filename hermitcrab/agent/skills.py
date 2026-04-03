"""Structured skill discovery, metadata parsing, and deterministic selection."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter

BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"
SELECTION_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "what",
    "when",
    "where",
    "which",
    "should",
    "would",
    "could",
    "have",
    "will",
    "them",
    "then",
    "than",
}


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """One discoverable skill plus parsed metadata."""

    name: str
    path: str
    source: str
    metadata: dict[str, Any]


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) with YAML frontmatter used for
    discovery and optional structured activation/runtime metadata.
    """

    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """List all available skills with workspace-first precedence."""
        skills = [
            {
                "name": record.name,
                "path": record.path,
                "source": record.source,
            }
            for record in self._iter_skill_records()
        ]
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def inspect_skills(self) -> list[dict[str, str | bool]]:
        """Return structured availability data for all discoverable skills."""
        inspected: list[dict[str, str | bool]] = []
        for skill in self.list_skills(filter_unavailable=False):
            skill_meta = self._get_skill_meta(skill["name"])
            available = self._check_requirements(skill_meta)
            inspected.append(
                {
                    **skill,
                    "available": available,
                    "description": self._get_skill_description(skill["name"]),
                    "missing_requirements": self._get_missing_requirements(skill_meta),
                }
            )
        return inspected

    def load_skill(self, name: str) -> str | None:
        """Load a skill by name."""
        record = self._get_skill_record(name)
        if not record:
            return None
        return Path(record.path).read_text(encoding="utf-8")

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """Load specific skills for inclusion in agent context."""
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                parts.append(f"### Skill: {name}\n\n{self._strip_frontmatter(content)}")
        return "\n\n---\n\n".join(parts) if parts else ""

    def select_skills(
        self,
        current_message: str | None,
        history: list[dict[str, str]] | None = None,
        *,
        max_skills: int = 3,
    ) -> list[str]:
        """Deterministically shortlist relevant skills for the active turn."""
        message = (current_message or "").strip()
        if not message:
            return []

        query_text = self._build_selection_query(message, history or [])
        query_lower = query_text.lower()
        query_tokens = self._selection_tokens(query_text)

        scored: list[tuple[int, str]] = []
        for record in self._iter_skill_records(filter_unavailable=True):
            name = record.name
            metadata = record.metadata
            description = self._get_skill_description(name)
            activation = self._get_activation_metadata(name)
            aliases = self._skill_aliases(name, metadata)
            tags = {
                str(tag).strip().lower()
                for tag in activation.get("tags", [])
                if str(tag).strip()
            }
            keywords = {
                str(keyword).strip().lower()
                for keyword in activation.get("keywords", [])
                if str(keyword).strip()
            }

            score = 0

            exact_aliases = {alias for alias in aliases if alias}
            if any(alias and alias in query_lower for alias in exact_aliases):
                score += 100

            tag_hits = {tag for tag in tags if tag in query_tokens}
            if tag_hits:
                score += min(36, 12 * len(tag_hits))

            keyword_hits = {keyword for keyword in keywords if keyword in query_tokens}
            if keyword_hits:
                score += min(36, 12 * len(keyword_hits))

            skill_tokens = (
                self._selection_tokens(name)
                | self._selection_tokens(description)
                | tags
                | keywords
            )
            overlap = query_tokens & skill_tokens
            if overlap:
                score += min(40, 8 * len(overlap))

            if score > 0:
                scored.append((score, name))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [name for _, name in scored[:max_skills]]

    def build_skills_summary(self, exclude_names: set[str] | None = None) -> str:
        """Build a compact XML discovery index of all skills."""
        exclude_names = exclude_names or set()
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(text: str) -> str:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for skill in all_skills:
            if skill["name"] in exclude_names:
                continue
            name = escape_xml(skill["name"])
            path = skill["path"]
            desc = escape_xml(self._get_skill_description(skill["name"]))
            skill_meta = self._get_skill_meta(skill["name"])
            available = self._check_requirements(skill_meta)

            lines.append(f'  <skill available="{str(available).lower()}">')
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")

            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")

            lines.append("  </skill>")
        lines.append("</skills>")

        if len(lines) == 2:
            return ""
        return "\n".join(lines)

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        result = []
        for record in self._iter_skill_records(filter_unavailable=True):
            meta = record.metadata
            skill_meta = self._get_skill_meta(record.name)
            if skill_meta.get("always") or meta.get("always"):
                result.append(record.name)
        return result

    def get_skill_metadata(self, name: str) -> dict[str, Any] | None:
        """Return parsed frontmatter metadata for a skill."""
        record = self._get_skill_record(name)
        return dict(record.metadata) if record else None

    def get_skill_record(self, name: str) -> SkillRecord | None:
        """Public helper for callers that need path plus parsed metadata."""
        return self._get_skill_record(name)

    def get_skill_runtime_metadata(self, name: str) -> dict[str, Any]:
        """Return structured runtime metadata for a skill when present."""
        return self._normalize_mapping(self._get_skill_meta(name).get("workflow"))

    def get_skill_activation_metadata(self, name: str) -> dict[str, Any]:
        """Return structured activation metadata for a skill when present."""
        return self._normalize_mapping(self._get_skill_meta(name).get("activation"))

    def _iter_skill_records(self, filter_unavailable: bool = False) -> list[SkillRecord]:
        """Return discoverable skills with workspace-first precedence."""
        records: list[SkillRecord] = []
        seen: set[str] = set()

        def _scan(root: Path, source: str) -> None:
            if not root.exists():
                return
            for skill_dir in sorted(root.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    continue
                record = self._build_skill_record(skill_file, source)
                if not record or record.name in seen:
                    continue
                if filter_unavailable and not self._check_requirements(
                    self._get_skill_meta(record.name)
                ):
                    continue
                seen.add(record.name)
                records.append(record)

        _scan(self.workspace_skills, "workspace")
        if self.builtin_skills:
            _scan(self.builtin_skills, "builtin")
        return records

    def _build_skill_record(self, skill_file: Path, source: str) -> SkillRecord | None:
        try:
            post = frontmatter.load(skill_file)
        except Exception:
            return None

        metadata = dict(post.metadata or {})
        name = str(metadata.get("name") or skill_file.parent.name).strip()
        if not name:
            name = skill_file.parent.name
        return SkillRecord(name=name, path=str(skill_file), source=source, metadata=metadata)

    def _get_skill_record(self, name: str) -> SkillRecord | None:
        for record in self._iter_skill_records(filter_unavailable=False):
            if record.name == name:
                return record
        return None

    def _get_missing_requirements(self, skill_meta: dict[str, Any]) -> str:
        missing = []
        requires = self._normalize_mapping(skill_meta.get("requires"))
        for binary in requires.get("bins", []) or []:
            if not shutil.which(str(binary)):
                missing.append(f"CLI: {binary}")
        for env_var in requires.get("env", []) or []:
            if not os.environ.get(str(env_var)):
                missing.append(f"ENV: {env_var}")
        return ", ".join(missing)

    def _get_skill_description(self, name: str) -> str:
        meta = self.get_skill_metadata(name)
        description = meta.get("description") if meta else None
        if isinstance(description, str) and description.strip():
            return description.strip()
        return name

    def _strip_frontmatter(self, content: str) -> str:
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end() :].strip()
        return content

    def _check_requirements(self, skill_meta: dict[str, Any]) -> bool:
        requires = self._normalize_mapping(skill_meta.get("requires"))
        for binary in requires.get("bins", []) or []:
            if not shutil.which(str(binary)):
                return False
        for env_var in requires.get("env", []) or []:
            if not os.environ.get(str(env_var)):
                return False
        return True

    def _get_skill_meta(self, name: str) -> dict[str, Any]:
        meta = self.get_skill_metadata(name) or {}
        hermitcrab_meta = self._extract_hermitcrab_metadata(meta)
        skill_meta = dict(hermitcrab_meta)

        if "always" in meta and "always" not in skill_meta:
            skill_meta["always"] = meta["always"]
        if "description" in meta and "description" not in skill_meta:
            skill_meta["description"] = meta["description"]
        return skill_meta

    def _extract_hermitcrab_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        raw = metadata.get("metadata")
        if isinstance(raw, dict):
            if isinstance(raw.get("hermitcrab"), dict):
                return dict(raw["hermitcrab"])
            if isinstance(raw.get("openclaw"), dict):
                return dict(raw["openclaw"])
            return {}
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            if isinstance(data, dict):
                nested = data.get("hermitcrab", data.get("openclaw", {}))
                if isinstance(nested, dict):
                    return dict(nested)
        return {}

    @staticmethod
    def _normalize_mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _get_activation_metadata(self, name: str) -> dict[str, Any]:
        activation = self._normalize_mapping(self._get_skill_meta(name).get("activation"))
        aliases = activation.get("aliases")
        if isinstance(aliases, str):
            activation["aliases"] = [aliases]
        keywords = activation.get("keywords")
        if isinstance(keywords, str):
            activation["keywords"] = [keywords]
        tags = activation.get("tags")
        if isinstance(tags, str):
            activation["tags"] = [tags]
        return activation

    def _skill_aliases(self, name: str, metadata: dict[str, Any]) -> set[str]:
        activation = self._get_activation_metadata(name)
        aliases = {
            name.lower(),
            name.lower().replace("_", "-"),
            name.lower().replace("-", "_"),
        }

        raw_name = metadata.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            aliases.add(raw_name.strip().lower())

        for alias in activation.get("aliases", []) or []:
            normalized = str(alias).strip().lower()
            if normalized:
                aliases.add(normalized)
        return {alias for alias in aliases if alias and len(alias) <= 80}

    def _build_selection_query(
        self,
        current_message: str,
        history: list[dict[str, str]],
    ) -> str:
        parts = [current_message]
        user_turns: list[str] = []
        for msg in reversed(history):
            if msg.get("role") != "user":
                continue
            content = (msg.get("content") or "").strip()
            if content:
                user_turns.append(content)
            if len(user_turns) >= 2:
                break
        parts.extend(reversed(user_turns))
        return " ".join(parts)

    def _selection_tokens(self, text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9][a-z0-9_-]+", text.lower())
            if len(token) >= 3 and token not in SELECTION_STOPWORDS
        }
