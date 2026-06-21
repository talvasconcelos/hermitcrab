"""Distillation and memory-commit helpers for background cognition."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
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
            filter_reason = self.distilled_candidate_filter_reason(candidate)
            if filter_reason is not None:
                self._audit_distillation_candidate("filtered", candidate, reason=filter_reason)
                logger.info("Distillation filtered candidate '{}' ({})", candidate.title, filter_reason)
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
            self._audit_distillation_candidate("committed", candidate, reason="committed")
        except Exception as exc:
            self._audit_distillation_candidate("failed", candidate, reason=str(exc))
            logger.error("Failed to commit candidate to memory: {}: {}", candidate.title, exc)

    def _audit_distillation_candidate(
        self,
        event: str,
        candidate: AtomicCandidate,
        *,
        reason: str,
    ) -> None:
        """Append a local audit event for distilled candidate decisions."""
        audit_path = self.memory.memory_dir / "distillation_audit.jsonl"
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "reason": reason,
            "type": candidate.type.value,
            "title": candidate.title,
            "source_session": candidate.source_session,
            "skip_reason": candidate.skip_reason,
            "evidence": candidate.evidence,
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def _audit_distillation_payload(self, event: str, session_key: str, *, reason: str) -> None:
        audit_path = self.memory.memory_dir / "distillation_audit.jsonl"
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "reason": reason,
            "source_session": session_key,
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def _audit_raw_candidate(
        self,
        event: str,
        session_key: str,
        candidate_data: Any,
        *,
        reason: str,
    ) -> None:
        title = candidate_data.get("title", "unknown") if isinstance(candidate_data, dict) else "unknown"
        candidate_type = candidate_data.get("type", "unknown") if isinstance(candidate_data, dict) else "unknown"
        audit_path = self.memory.memory_dir / "distillation_audit.jsonl"
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "reason": reason,
            "type": candidate_type,
            "title": title,
            "source_session": session_key,
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    @staticmethod
    def normalize_memory_text(text: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9\s]+", " ", text.lower()).split())

    @classmethod
    def _messages_text(cls, messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            if isinstance(message.get("tool_calls"), list):
                parts.extend(cls.iter_strings(message["tool_calls"]))
        return cls.normalize_memory_text(" ".join(parts))

    def _candidate_evidence_is_grounded(self, candidate: AtomicCandidate, session_text: str) -> bool:
        if candidate.type == CandidateType.IGNORED:
            return True
        if not session_text:
            return True
        evidence = self.normalize_memory_text(candidate.evidence or "")
        if not evidence:
            return False
        if evidence in session_text:
            return True
        evidence_tokens = set(evidence.split())
        if not evidence_tokens:
            return False
        session_tokens = set(session_text.split())
        overlap = evidence_tokens & session_tokens
        return len(overlap) / len(evidence_tokens) >= 0.8

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
        return self.distilled_candidate_filter_reason(candidate) is None

    def distilled_candidate_filter_reason(self, candidate: AtomicCandidate) -> str | None:
        allowed_types = {CandidateType.FACT, CandidateType.GOAL, CandidateType.TASK}
        if candidate.type == CandidateType.IGNORED:
            return candidate.skip_reason or "ignored"
        if candidate.confidence < 0.65:
            return "low_confidence"

        if candidate.type == CandidateType.DECISION:
            has_rationale = bool((candidate.decision_rationale or "").strip())
            has_status = candidate.decision_status is not None
            if candidate.confidence < 0.9 or not has_rationale or not has_status:
                return "decision_missing_rationale_or_status"
        elif candidate.type == CandidateType.FACT:
            if self.looks_like_bootstrap_instruction(candidate):
                return "bootstrap_instruction"
        elif candidate.type not in allowed_types:
            return "unsupported_type"

        duplicates = self.find_existing_memory_duplicates(candidate)
        if duplicates:
            if candidate.type == CandidateType.FACT:
                normalized_title = self.normalize_memory_text(candidate.title)
                normalized_content = self.normalize_memory_text(candidate.content)
                for existing in duplicates:
                    if self.normalize_memory_text(existing.title) == normalized_title:
                        if self.normalize_memory_text(existing.content) != normalized_content:
                            return None
            return "duplicate"
        return None

    @staticmethod
    def looks_like_bootstrap_instruction(candidate: AtomicCandidate) -> bool:
        normalized_tags = {
            re.sub(r"[^a-z0-9\s]+", " ", str(tag).lower()).strip()
            for tag in candidate.tags or []
            if str(tag).strip()
        }
        if not candidate.title.strip() and not candidate.content.strip():
            return True
        operational_tags = {
            "agent",
            "assistant",
            "subagent",
            "tool",
            "workflow",
            "process",
            "prompt",
            "journal",
            "reflection",
            "memory",
            "delegation",
            "coordination",
        }
        return bool(normalized_tags & operational_tags)

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
                await self._commit_distillation_response(content, session.key, messages)
            except json.JSONDecodeError as exc:
                logger.warning("Distillation response not valid JSON: {}: {}", session.key, exc)
            except Exception as exc:
                logger.warning("Distillation LLM failed: {}: {}", session.key, exc)
        except Exception as exc:
            logger.warning("Distillation failed (non-fatal): {}: {}", session.key, exc)

    def _related_memory_for_distillation(self, messages: list[dict[str, Any]]) -> str:
        """Retrieve existing memory before asking the model to propose candidates."""
        queries = []
        for message in messages[:20]:
            content = str(message.get("content") or "").strip()
            if content:
                queries.append(content[:500])
        return self.memory.get_relevant_context_for_queries(
            queries,
            limit=6,
            max_chars=2500,
            max_item_chars=500,
        )

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
        )
        related_memory = self._related_memory_for_distillation(messages)
        if related_memory:
            prompt += (
                "Existing related memory (check this before proposing candidates):\n"
                f"{related_memory}\n\n"
                "If the session repeats existing memory, return an ignored candidate with skip_reason.\n"
                "If the session corrects existing memory, propose the smallest update and cite evidence.\n"
                "Only create new candidates when the learning is not already present.\n"
                "Allowed actions in candidate extra: create, update, reuse, ignore.\n\n"
            )
        prompt += "Session content:\n"
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
            "Do not convert corrections about agent behavior, delegation, tool discipline, journaling, reflection, or prompt handling into FACTS; those belong to reflection/bootstrap guidance and should be skipped here.\n"
            "For TASK type: task_assignee (required), task_status, task_deadline, task_priority\n"
            "For GOAL type: goal_status, goal_priority, goal_horizon\n"
            "For DECISION type: decision_status, decision_rationale, decision_supersedes\n"
            "Be conservative. Skip weak, duplicate, or speculative items."
        )
        return prompt

    async def _commit_distillation_response(
        self,
        content: str,
        session_key: str,
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        data = self._extract_distillation_payload(content, session_key)
        if data is None:
            self._audit_distillation_payload("parse_failed", session_key, reason="invalid_json_payload")
            return

        candidates = data.get("candidates", [])
        validated_count = 0
        session_text = self._messages_text(messages or [])
        for candidate_data in candidates:
            try:
                candidate = self._parse_candidate(candidate_data, session_key)
                if candidate is None:
                    self._audit_raw_candidate(
                        "validation_failed",
                        session_key,
                        candidate_data,
                        reason="invalid_candidate",
                    )
                    continue
                if not self._candidate_evidence_is_grounded(candidate, session_text):
                    self._audit_distillation_candidate(
                        "validation_failed",
                        candidate,
                        reason="evidence_not_in_session",
                    )
                    continue
                self.commit_candidate_to_memory(candidate)
                validated_count += 1
            except Exception as exc:
                title = (
                    candidate_data.get("title", "unknown")
                    if isinstance(candidate_data, dict)
                    else "unknown"
                )
                self._audit_raw_candidate(
                    "validation_failed",
                    session_key,
                    candidate_data,
                    reason=str(exc),
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
        missing_errors = []
        if not str(candidate_data.get("content", "")).strip():
            missing_errors.append("Content is required")
        if candidate_data.get("type") != CandidateType.IGNORED.value and not str(
            candidate_data.get("evidence", "")
        ).strip():
            missing_errors.append("Evidence is required")
        if missing_errors:
            raise ValueError("; ".join(missing_errors))
        candidate = AtomicCandidate.from_dict(candidate_data)
        candidate.source_session = session_key
        errors = candidate.validate()
        if errors:
            logger.warning("Candidate validation failed: {}: {}", candidate.title, errors)
            raise ValueError("; ".join(errors))
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
