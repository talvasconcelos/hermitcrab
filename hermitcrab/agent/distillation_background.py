"""Distillation and memory-commit helpers for background cognition."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from hermitcrab.agent.distillation import AtomicCandidate, CandidateType
from hermitcrab.agent.memory import MemoryStore
from hermitcrab.utils.helpers import safe_filename


class DistillationManager:
    """Own distillation filtering, prompting, validation, and memory commits."""

    def __init__(
        self,
        *,
        workspace: Path,
        memory: MemoryStore,
        chat_callable: Callable[..., Awaitable[Any]],
        get_model_for_job: Callable[[Any], str | None],
        strip_think: Callable[[str | None], str | None],
        reasoning_effort: str | None,
    ) -> None:
        self.workspace = workspace
        self.memory = memory
        self.chat_callable = chat_callable
        self.get_model_for_job = get_model_for_job
        self.strip_think = strip_think
        self.reasoning_effort = reasoning_effort

    @staticmethod
    def iter_strings(obj: Any) -> list[str]:
        values: list[str] = []
        if isinstance(obj, str):
            return [obj]
        if isinstance(obj, dict):
            for value in obj.values():
                values.extend(DistillationManager.iter_strings(value))
        elif isinstance(obj, list):
            for item in obj:
                values.extend(DistillationManager.iter_strings(item))
        return values

    def tool_call_targets_scratchpad(self, tc: dict[str, Any], session_key: str) -> bool:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        args_raw = fn.get("arguments", {})
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw)
            except Exception:
                args = args_raw
        else:
            args = args_raw

        strings = self.iter_strings(args)
        scratchpad = (
            self.workspace / "scratchpads" / f"{safe_filename(session_key.replace(':', '_'))}.md"
        ).resolve()
        for value in strings:
            try:
                path = Path(value)
                path = (
                    (self.workspace / path).resolve() if not path.is_absolute() else path.resolve()
                )
            except Exception:
                continue
            if path == scratchpad:
                return True
        return False

    def filter_messages_for_distillation(
        self,
        messages: list[dict[str, Any]],
        session_key: str,
    ) -> list[dict[str, Any]]:
        excluded_tool_call_ids: set[str] = set()
        filtered: list[dict[str, Any]] = []

        for msg in messages:
            if msg.get("role") == "assistant" and isinstance(msg.get("tool_calls"), list):
                kept_calls = []
                for tc in msg["tool_calls"]:
                    if self.tool_call_targets_scratchpad(tc, session_key):
                        if tc_id := tc.get("id"):
                            excluded_tool_call_ids.add(tc_id)
                        continue
                    kept_calls.append(tc)

                if kept_calls != msg["tool_calls"]:
                    msg_copy = dict(msg)
                    if kept_calls:
                        msg_copy["tool_calls"] = kept_calls
                    else:
                        msg_copy.pop("tool_calls", None)
                    filtered.append(msg_copy)
                    continue

            if msg.get("role") == "tool" and msg.get("tool_call_id") in excluded_tool_call_ids:
                continue
            filtered.append(msg)

        return filtered

    def commit_candidate_to_memory(self, candidate: AtomicCandidate) -> None:
        try:
            if not self.should_commit_distilled_candidate(candidate):
                logger.info("Distillation filtered candidate '{}'", candidate.title)
                return

            params = candidate.to_memory_params()
            if candidate.type == CandidateType.FACT:
                self.memory.write_fact(**params)
                logger.info("Memory commit: fact '{}'", candidate.title)
            elif candidate.type == CandidateType.DECISION:
                self.memory.write_decision(**params)
                logger.info("Memory commit: decision '{}'", candidate.title)
            elif candidate.type == CandidateType.GOAL:
                self.memory.write_goal(**params)
                logger.info("Memory commit: goal '{}'", candidate.title)
            elif candidate.type == CandidateType.TASK:
                if not params.get("assignee"):
                    params["assignee"] = "distilled"
                self.memory.write_task(**params)
                logger.info("Memory commit: task '{}'", candidate.title)
            elif candidate.type == CandidateType.REFLECTION:
                self.memory.write_reflection(**params)
                logger.info("Memory commit: reflection '{}'", candidate.title)
        except Exception as exc:
            logger.error("Failed to commit candidate to memory: {}: {}", candidate.title, exc)

    @staticmethod
    def normalize_memory_text(text: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9\s]+", " ", text.lower()).split())

    def is_near_duplicate_memory_item(self, candidate: AtomicCandidate, existing: Any) -> bool:
        title_ratio = SequenceMatcher(
            None,
            self.normalize_memory_text(candidate.title),
            self.normalize_memory_text(existing.title),
        ).ratio()
        content_ratio = SequenceMatcher(
            None,
            self.normalize_memory_text(candidate.content),
            self.normalize_memory_text(existing.content),
        ).ratio()
        return title_ratio >= 0.9 or (title_ratio >= 0.8 and content_ratio >= 0.85)

    def find_existing_memory_duplicates(self, candidate: AtomicCandidate) -> list[Any]:
        category_map = {
            CandidateType.FACT: "facts",
            CandidateType.DECISION: "decisions",
            CandidateType.GOAL: "goals",
            CandidateType.TASK: "tasks",
            CandidateType.REFLECTION: "reflections",
        }
        existing = self.memory.read_memory(category_map[candidate.type])
        return [item for item in existing if self.is_near_duplicate_memory_item(candidate, item)]

    def should_commit_distilled_candidate(self, candidate: AtomicCandidate) -> bool:
        allowed_types = {CandidateType.FACT, CandidateType.GOAL, CandidateType.TASK}
        if candidate.type == CandidateType.DECISION:
            has_rationale = bool((candidate.decision_rationale or "").strip())
            if candidate.confidence < 0.9 or not has_rationale:
                return False
            if self.looks_like_non_decision_artifact(candidate):
                return False
        elif candidate.type not in allowed_types:
            return False

        if candidate.confidence < 0.65:
            return False
        return not self.find_existing_memory_duplicates(candidate)

    @staticmethod
    def looks_like_non_decision_artifact(candidate: AtomicCandidate) -> bool:
        normalized = " ".join(
            re.sub(
                r"[^a-z0-9\s]+",
                " ",
                " ".join(
                    filter(None, [candidate.title, candidate.content, candidate.decision_rationale])
                ).lower(),
            ).split()
        )
        if not normalized:
            return True

        report_markers = (
            "recommendation",
            "recommended",
            "report",
            "analysis",
            "placeholder",
            "not explicitly stated",
            "not prioritized yet",
            "possible decision",
            "tentative",
            "option list",
        )
        proposal_markers = (
            "we should ",
            "should use",
            "could use",
            "might use",
            "proposal",
            "proposed",
        )
        return any(marker in normalized for marker in report_markers + proposal_markers)

    async def distill_session(self, session: Any, distillation_job_class: Any) -> None:
        try:
            logger.debug("Distillation started: {}", session.key)
            messages = self.filter_messages_for_distillation(session.messages, session.key)
            if not messages:
                logger.debug("Distillation skipped (no messages after filtering): {}", session.key)
                return

            prompt = self._build_distillation_prompt(messages)
            model = self.get_model_for_job(distillation_job_class)
            if not model:
                logger.debug("Distillation skipped (no model): {}", session.key)
                return

            try:
                response = await self.chat_callable(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    temperature=0.1,
                    max_tokens=2048,
                    job_class=distillation_job_class,
                    reasoning_effort=self.reasoning_effort,
                )
                content = self.strip_think(response.content)
                if not content:
                    return
                await self._commit_distillation_response(content, session.key)
            except json.JSONDecodeError as exc:
                logger.warning("Distillation response not valid JSON: {}: {}", session.key, exc)
            except Exception as exc:
                logger.warning("Distillation LLM failed: {}: {}", session.key, exc)
        except Exception as exc:
            logger.warning("Distillation failed (non-fatal): {}: {}", session.key, exc)

    def _build_distillation_prompt(self, messages: list[dict[str, Any]]) -> str:
        prompt = (
            "Extract conservative atomic knowledge candidates from this session.\n\n"
            "Look for:\n"
            "- FACTS: User preferences, project context, established truths\n"
            "- DECISIONS: Architectural choices, trade-offs, locked decisions\n"
            "- GOALS: Objectives, outcomes the user wants to achieve\n"
            "- TASKS: Action items, todos, things to do (must include task_assignee)\n\n"
            "Do not produce reflections here.\n"
            "For TASK candidates, include task_assignee. Use 'user' for user tasks.\n\n"
            "Session content:\n"
        )
        for msg in messages[:50]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:500]
            if role == "user":
                prompt += f"User: {content}\n"
            elif role == "assistant":
                prompt += f"Assistant: {content}\n"
        prompt += (
            "\n\nReturn candidates as a JSON object with 'candidates' array.\n"
            "Each candidate must have: type, title, content.\n"
            "Optional: confidence (0-1), tags, and type-specific fields.\n"
            "Allowed types by default: fact, goal, task. Use decision only for clear locked choices with rationale.\n"
            "For TASK type: task_assignee (required), task_status, task_deadline, task_priority\n"
            "For GOAL type: goal_status, goal_priority, goal_horizon\n"
            "For DECISION type: decision_status, decision_rationale, decision_supersedes\n"
            "Be conservative. Skip weak, duplicate, or speculative items."
        )
        return prompt

    async def _commit_distillation_response(self, content: str, session_key: str) -> None:
        data = self._extract_distillation_payload(content, session_key)
        if data is None:
            return

        candidates = data.get("candidates", [])
        validated_count = 0
        for candidate_data in candidates:
            try:
                candidate = self._parse_candidate(candidate_data, session_key)
                if candidate is None:
                    continue
                self.commit_candidate_to_memory(candidate)
                validated_count += 1
            except Exception as exc:
                title = (
                    candidate_data.get("title", "unknown")
                    if isinstance(candidate_data, dict)
                    else "unknown"
                )
                logger.warning("Failed to parse candidate: {}: {}", title, exc)

        if validated_count > 0:
            logger.info(
                "Distillation complete: {} candidates from {}", validated_count, session_key
            )
        else:
            logger.debug("No valid candidates distilled: {}", session_key)

    @staticmethod
    def _extract_distillation_payload(content: str, session_key: str) -> dict[str, Any] | None:
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start < 0 or json_end <= json_start:
            return None
        data = json.loads(content[json_start:json_end])
        if not isinstance(data, dict):
            logger.warning(
                "Distillation response root is not an object: {} ({})",
                session_key,
                type(data).__name__,
            )
            return None
        return data

    @staticmethod
    def _parse_candidate(candidate_data: Any, session_key: str) -> AtomicCandidate | None:
        if not isinstance(candidate_data, dict):
            logger.debug(
                "Skipping non-dict distillation candidate for {}: {}",
                session_key,
                type(candidate_data).__name__,
            )
            return None
        candidate = AtomicCandidate.from_dict(candidate_data)
        candidate.source_session = session_key
        errors = candidate.validate()
        if errors:
            logger.warning("Candidate validation failed: {}: {}", candidate.title, errors)
            return None
        return candidate

    async def distill_session_from_messages(
        self,
        messages: list[dict[str, Any]],
        session_key: str,
        distillation_job_class: Any,
    ) -> None:
        class _SessionSnapshot:
            def __init__(self, snapshot_messages: list[dict[str, Any]], key: str):
                self.messages = snapshot_messages
                self.key = key

        await self.distill_session(_SessionSnapshot(messages, session_key), distillation_job_class)
