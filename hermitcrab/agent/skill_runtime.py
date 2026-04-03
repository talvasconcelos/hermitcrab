"""Structured procedural-skill runtime state for multi-step skill execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from hermitcrab.agent.skills import SkillsLoader


@dataclass(frozen=True, slots=True)
class SkillPhase:
    """One procedural phase declared by a skill."""

    id: str
    title: str
    instructions: str = ""
    required_tools: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProceduralSkillSpec:
    """Structured runtime contract for a procedural skill."""

    skill_name: str
    skill_path: str
    description: str
    phases: tuple[SkillPhase, ...]
    activation_aliases: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return bool(self.skill_name and self.phases)


@dataclass(slots=True)
class SkillRunState:
    """Persisted state for an active procedural skill."""

    skill_name: str
    skill_path: str
    origin_request: str
    current_phase_index: int = 0
    completed_phase_ids: list[str] = field(default_factory=list)
    observed_tools: list[str] = field(default_factory=list)
    observed_artifacts: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "skill_path": self.skill_path,
            "origin_request": self.origin_request,
            "current_phase_index": self.current_phase_index,
            "completed_phase_ids": list(self.completed_phase_ids),
            "observed_tools": list(self.observed_tools),
            "observed_artifacts": list(self.observed_artifacts),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None) -> SkillRunState | None:
        raw = metadata.get("active_skill_run") if metadata else None
        if not isinstance(raw, dict):
            return None
        skill_name = str(raw.get("skill_name") or "").strip()
        skill_path = str(raw.get("skill_path") or "").strip()
        origin_request = str(raw.get("origin_request") or "").strip()
        if not (skill_name and skill_path):
            return None
        return cls(
            skill_name=skill_name,
            skill_path=skill_path,
            origin_request=origin_request,
            current_phase_index=max(0, int(raw.get("current_phase_index", 0) or 0)),
            completed_phase_ids=[
                str(item).strip()
                for item in raw.get("completed_phase_ids", [])
                if str(item).strip()
            ],
            observed_tools=[
                str(item).strip() for item in raw.get("observed_tools", []) if str(item).strip()
            ],
            observed_artifacts=[
                str(item).strip()
                for item in raw.get("observed_artifacts", [])
                if str(item).strip()
            ],
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )


class SkillRuntimeManager:
    """Own structured activation and progression for procedural skills."""

    def __init__(self, workspace: Path, skills: SkillsLoader):
        self.workspace = workspace
        self.skills = skills

    def get_active_spec(self, metadata: dict[str, Any] | None) -> ProceduralSkillSpec | None:
        state = SkillRunState.from_metadata(metadata)
        if not state:
            return None
        return self.get_spec(state.skill_name)

    def get_spec(self, skill_name: str) -> ProceduralSkillSpec | None:
        record = self.skills.get_skill_record(skill_name)
        if not record:
            return None

        runtime = self.skills.get_skill_runtime_metadata(skill_name)
        if str(runtime.get("kind") or runtime.get("type") or "").strip().lower() != "workflow":
            return None

        raw_phases = runtime.get("phases")
        if not isinstance(raw_phases, list):
            return None

        phases: list[SkillPhase] = []
        for index, raw_phase in enumerate(raw_phases, start=1):
            if not isinstance(raw_phase, dict):
                continue
            phase_id = str(raw_phase.get("id") or raw_phase.get("name") or f"phase-{index}").strip()
            title = str(raw_phase.get("title") or raw_phase.get("name") or phase_id).strip()
            instructions = str(raw_phase.get("instructions") or raw_phase.get("goal") or "").strip()
            completion = raw_phase.get("completion")
            if not isinstance(completion, dict):
                completion = {}
            required_tools = tuple(
                str(item).strip() for item in completion.get("tools", []) if str(item).strip()
            )
            required_artifacts = tuple(
                str(item).strip()
                for item in completion.get("artifacts", [])
                if str(item).strip()
            )
            phases.append(
                SkillPhase(
                    id=phase_id,
                    title=title,
                    instructions=instructions,
                    required_tools=required_tools,
                    required_artifacts=required_artifacts,
                )
            )

        activation = self.skills.get_skill_activation_metadata(skill_name)
        aliases = tuple(str(item).strip() for item in activation.get("aliases", []) if str(item).strip())

        spec = ProceduralSkillSpec(
            skill_name=record.name,
            skill_path=record.path,
            description=str(record.metadata.get("description") or record.name).strip(),
            phases=tuple(phases),
            activation_aliases=aliases,
        )
        return spec if spec.is_valid else None

    def maybe_activate(
        self,
        session_metadata: dict[str, Any],
        *,
        current_message: str,
        history: list[dict[str, Any]],
    ) -> SkillRunState | None:
        existing = SkillRunState.from_metadata(session_metadata)
        if existing and self.get_spec(existing.skill_name):
            return existing

        for skill_name in self.skills.select_skills(current_message, history, max_skills=3):
            spec = self.get_spec(skill_name)
            if not spec:
                continue
            now = datetime.now().isoformat()
            state = SkillRunState(
                skill_name=spec.skill_name,
                skill_path=spec.skill_path,
                origin_request=current_message.strip(),
                created_at=now,
                updated_at=now,
            )
            session_metadata["active_skill_run"] = state.to_metadata()
            return state
        return None

    def build_turn_guidance(self, session_metadata: dict[str, Any]) -> str | None:
        state = SkillRunState.from_metadata(session_metadata)
        if not state:
            return None
        spec = self.get_spec(state.skill_name)
        if not spec or state.current_phase_index >= len(spec.phases):
            return None

        phase = spec.phases[state.current_phase_index]
        completed = ", ".join(state.completed_phase_ids) if state.completed_phase_ids else "none"
        observed_tools = ", ".join(state.observed_tools[-6:]) if state.observed_tools else "none"
        observed_artifacts = (
            ", ".join(state.observed_artifacts[-6:]) if state.observed_artifacts else "none"
        )
        required_tools = ", ".join(phase.required_tools) if phase.required_tools else "none"
        required_artifacts = (
            ", ".join(phase.required_artifacts) if phase.required_artifacts else "none"
        )

        return (
            f"You are executing the procedural skill `{spec.skill_name}` from `{spec.skill_path}`.\n"
            f"Skill description: {spec.description}\n"
            f"Current phase: {phase.id} ({phase.title}) [{state.current_phase_index + 1}/{len(spec.phases)}]\n"
            f"Phase instructions: {phase.instructions or 'Use the skill body for the detailed procedure.'}\n"
            f"Completed phases: {completed}\n"
            f"Observed tools so far: {observed_tools}\n"
            f"Observed artifacts so far: {observed_artifacts}\n"
            f"Current phase requires tools: {required_tools}\n"
            f"Current phase requires artifacts: {required_artifacts}\n"
            "Do not claim the skill is complete until the current phase requirements are satisfied. "
            "If the work is blocked, state the exact blocker."
        )

    def update_after_turn(
        self,
        session_metadata: dict[str, Any],
        *,
        result_messages: list[dict[str, Any]],
        tools_used: list[str],
    ) -> None:
        state = SkillRunState.from_metadata(session_metadata)
        if not state:
            return
        spec = self.get_spec(state.skill_name)
        if not spec:
            session_metadata.pop("active_skill_run", None)
            return
        if state.current_phase_index >= len(spec.phases):
            session_metadata.pop("active_skill_run", None)
            return

        observed_artifacts = self._extract_artifact_paths(result_messages)
        if tools_used:
            state.observed_tools = list(dict.fromkeys([*state.observed_tools, *tools_used]))[-12:]
        if observed_artifacts:
            state.observed_artifacts = list(
                dict.fromkeys([*state.observed_artifacts, *observed_artifacts])
            )[-24:]
        state.updated_at = datetime.now().isoformat()

        current_phase = spec.phases[state.current_phase_index]
        if self._phase_completed(current_phase, state):
            if current_phase.id not in state.completed_phase_ids:
                state.completed_phase_ids.append(current_phase.id)
            state.current_phase_index += 1

        if state.current_phase_index >= len(spec.phases):
            session_metadata.pop("active_skill_run", None)
        else:
            session_metadata["active_skill_run"] = state.to_metadata()

    def _phase_completed(self, phase: SkillPhase, state: SkillRunState) -> bool:
        tools_ok = all(tool in state.observed_tools for tool in phase.required_tools)
        artifacts_ok = all(
            self._artifact_requirement_met(requirement, state.observed_artifacts)
            for requirement in phase.required_artifacts
        )

        if phase.required_tools or phase.required_artifacts:
            return tools_ok and artifacts_ok
        return bool(state.observed_tools or state.observed_artifacts)

    def _artifact_requirement_met(self, requirement: str, observed_artifacts: list[str]) -> bool:
        requirement = requirement.strip()
        if not requirement:
            return True
        for observed in observed_artifacts:
            observed_path = Path(observed)
            if observed == requirement or observed_path.match(requirement):
                return True
            absolute_observed = self.workspace / observed
            if absolute_observed.match(requirement):
                return True
        return False

    @staticmethod
    def _extract_artifact_paths(messages: list[dict[str, Any]]) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            for tool_call in msg.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") or {}
                tool_name = function.get("name")
                if tool_name not in {"write_file", "edit_file"}:
                    continue
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                path = str(arguments.get("path") or "").strip()
                if path and path not in seen:
                    seen.add(path)
                    paths.append(path)
        return paths
