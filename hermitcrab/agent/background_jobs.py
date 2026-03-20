"""Background cognition helpers for journal, distillation, and reflection."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Awaitable, Callable

import json_repair
from loguru import logger

from hermitcrab.agent.distillation import AtomicCandidate, CandidateType
from hermitcrab.agent.journal import JournalStore
from hermitcrab.agent.memory import MemoryStore
from hermitcrab.agent.message_preparation import (
    clean_snippet,
    extract_subagent_task,
    is_low_signal_journal_body,
    is_subagent_completion_prompt,
    is_transition_assistant_message,
)
from hermitcrab.agent.reflection import ReflectionService
from hermitcrab.utils.helpers import safe_filename


@dataclass
class SessionDigest:
    """Deterministic summary of a session for background cognition."""

    session_key: str
    channel: str
    chat_id: str
    first_timestamp: str
    last_timestamp: str
    event_lines: list[str]
    user_requests: list[str]
    user_corrections: list[str]
    outcomes: list[str]
    failures: list[str]
    wikilinks: list[str]


class BackgroundJobManager:
    """Own background task scheduling and session-end cognition work."""

    _TOOL_RESULT_MAX_CHARS = 500

    def __init__(
        self,
        *,
        workspace: Path,
        journal: JournalStore,
        memory: MemoryStore,
        reflection_service: ReflectionService,
        chat_callable: Callable[..., Awaitable[Any]],
        get_model_for_job: Callable[[Any], str | None],
        strip_think: Callable[[str | None], str | None],
        reasoning_effort: str | None,
    ) -> None:
        self.workspace = workspace
        self.journal = journal
        self.memory = memory
        self.reflection_service = reflection_service
        self.chat_callable = chat_callable
        self.get_model_for_job = get_model_for_job
        self.strip_think = strip_think
        self.reasoning_effort = reasoning_effort
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def schedule_background(self, coro: Awaitable[Any], task_name: str) -> None:
        """Schedule a background task and keep non-fatal failures isolated."""

        async def _wrapped() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                logger.debug("Background task cancelled: {}", task_name)
            except Exception as exc:
                logger.warning("Background task failed (non-fatal): {}: {}", task_name, exc)
            finally:
                self._background_tasks.discard(asyncio.current_task())

        task = asyncio.create_task(_wrapped(), name=task_name)
        self._background_tasks.add(task)

    async def shutdown_background_tasks(self) -> None:
        """Cancel and await all tracked background tasks."""
        if not self._background_tasks:
            return
        pending = list(self._background_tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._background_tasks.clear()

    async def wait_for_background_tasks(self, timeout_s: float = 5.0) -> tuple[int, int]:
        """Wait for currently scheduled background tasks to finish."""
        if not self._background_tasks:
            return 0, 0

        tasks = [task for task in self._background_tasks if not task.done()]
        if not tasks:
            return 0, 0

        done, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout_s))
        if pending:
            logger.warning(
                "Background tasks still running after {:.1f}s: {} pending",
                timeout_s,
                len(pending),
            )
        return len(done), len(pending)

    @staticmethod
    def derive_channel_chat(session_key: str) -> tuple[str, str]:
        if ":" not in session_key:
            return session_key, "direct"
        return session_key.split(":", 1)

    @staticmethod
    def safe_iso_timestamp(value: str | None) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def extract_tool_name(call: dict[str, Any]) -> str:
        function = call.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return function["name"]
        if isinstance(call.get("name"), str):
            return call["name"]
        return "unknown"

    @staticmethod
    def extract_tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
        function = call.get("function")
        raw_arguments = (
            function.get("arguments") if isinstance(function, dict) else call.get("arguments")
        )
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError:
                try:
                    parsed = json_repair.loads(raw_arguments)
                except Exception:
                    return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def build_journal_event_trace(digest: SessionDigest) -> list[str]:
        filtered: list[str] = []
        for line in digest.event_lines:
            normalized = line.lower()
            if "assistant used " in normalized and any(
                marker in normalized
                for marker in (
                    "read_memory",
                    "search_memory",
                    "read_file",
                    "list_dir",
                    "spawn",
                    "search_knowledge",
                )
            ):
                continue
            filtered.append(line)
        return filtered or digest.event_lines

    def build_session_digest(
        self, messages: list[dict[str, Any]], session_key: str
    ) -> SessionDigest:
        channel, chat_id = self.derive_channel_chat(session_key)
        timestamps = [
            self.safe_iso_timestamp(msg.get("timestamp"))
            for msg in messages
            if msg.get("role") in {"user", "assistant", "tool"}
        ]
        first_timestamp = timestamps[0] if timestamps else self.safe_iso_timestamp(None)
        last_timestamp = timestamps[-1] if timestamps else first_timestamp

        event_lines: list[str] = []
        user_requests: list[str] = []
        user_corrections: list[str] = []
        outcomes: list[str] = []
        failures: list[str] = []
        wikilinks: list[str] = []

        for msg in messages[-40:]:
            role = msg.get("role")
            content = clean_snippet(msg.get("content"))
            if role == "user":
                if not content:
                    continue
                raw_content = str(msg.get("content") or "")
                if is_subagent_completion_prompt(raw_content):
                    task = extract_subagent_task(raw_content)
                    if task:
                        event_lines.append(f"- Subagent reported back for task: {task}")
                    continue
                event_lines.append(f"- User: {content}")
                user_requests.append(content)
                lowered = content.lower()
                if any(
                    marker in lowered
                    for marker in ("don't", "do not", "stop", "instead", "should", "not ")
                ):
                    user_corrections.append(content)
            elif role == "assistant":
                tool_calls = (
                    msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else []
                )
                if content and not is_transition_assistant_message(content, tool_calls):
                    event_lines.append(f"- Assistant: {content}")
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    tool_name = self.extract_tool_name(call)
                    arguments = self.extract_tool_arguments(call)
                    title = clean_snippet(arguments.get("title"), max_chars=80)
                    if title and tool_name.startswith("write_"):
                        wikilinks.append(f"[[{title}]]")
                    if title and tool_name in {
                        "write_task",
                        "write_goal",
                        "write_decision",
                        "write_fact",
                    }:
                        event_lines.append(f"- Assistant saved {tool_name[6:]} [[{title}]].")
                    else:
                        focus = title or clean_snippet(
                            arguments.get("query") or arguments.get("path")
                        )
                        if focus:
                            event_lines.append(f"- Assistant used {tool_name}: {focus}")
            elif role == "tool":
                tool_name = clean_snippet(msg.get("name"), max_chars=60) or "tool"
                if not content:
                    continue
                lowered = content.lower()
                if lowered.startswith("error") or "tool error" in lowered or "failed" in lowered:
                    failure = f"{tool_name}: {content}"
                    failures.append(failure)
                    event_lines.append(f"- Tool failure ({tool_name}): {content}")
                elif lowered.startswith(
                    ("task saved:", "goal saved:", "decision saved:", "fact saved:")
                ):
                    outcomes.append(content)

        unique_links: list[str] = []
        seen_links: set[str] = set()
        for link in wikilinks:
            if link not in seen_links:
                unique_links.append(link)
                seen_links.add(link)

        return SessionDigest(
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
            event_lines=event_lines[-20:] or ["- No significant events captured."],
            user_requests=user_requests[-8:],
            user_corrections=user_corrections[-6:],
            outcomes=outcomes[-8:],
            failures=failures[-6:],
            wikilinks=unique_links[:10],
        )

    @staticmethod
    def format_digest_timestamp(value: str) -> str:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
        return dt.astimezone(timezone.utc).strftime("%H:%M UTC")

    def format_journal_entry(self, digest: SessionDigest, body: str) -> str:
        heading = (
            f"## {self.format_digest_timestamp(digest.last_timestamp)} · {digest.channel} · "
            f"`{digest.session_key}`"
        )
        meta = f"_Session:_ `{digest.session_key}`  \n_Channel:_ `{digest.channel}`"
        if digest.wikilinks:
            meta += f"  \n_Links:_ {' '.join(digest.wikilinks[:4])}"
        return f"{heading}\n\n{meta}\n\n{body.strip()}"

    @staticmethod
    def build_fallback_journal_body(digest: SessionDigest) -> str:
        request = (
            digest.user_requests[-1]
            if digest.user_requests
            else "The user continued the conversation."
        )
        parts = [f"I worked on {request}"]
        if digest.outcomes:
            parts.append(f"The clearest outcome was {digest.outcomes[-1]}")
        if digest.failures:
            parts.append(f"A notable snag was {digest.failures[-1]}")
        if digest.wikilinks:
            parts.append(f"Related notes: {' '.join(digest.wikilinks[:4])}")
        return " ".join(parts)

    async def synthesize_journal(self, session: Any, journal_job_class: Any) -> None:
        try:
            messages = session.messages
            if not messages:
                return
            digest = self.build_session_digest(messages, session.key)
            candidate_links = ", ".join(digest.wikilinks) if digest.wikilinks else "none"
            journal_event_trace = self.build_journal_event_trace(digest)
            prompt = (
                "Write a short first-person journal entry about what happened in this session.\n"
                "Sound like a useful human journal, not telemetry.\n"
                "Focus on what the user wanted, what I tried, what changed, and the outcome.\n"
                "Use Obsidian-style wikilinks when referencing tasks, goals, decisions, reflections, or named work items.\n"
                "Do not mention counts of messages, requests, or tool calls.\n\n"
                f"Session: {digest.session_key}\n"
                f"Channel: {digest.channel}\n"
                f"Chat: {digest.chat_id}\n"
                f"Time range: {digest.first_timestamp} -> {digest.last_timestamp}\n"
                f"Candidate wikilinks: {candidate_links}\n\n"
                "Event trace:\n"
                f"{chr(10).join(journal_event_trace[:18])}\n\n"
                "Write 3-6 sentences only."
            )

            model = self.get_model_for_job(journal_job_class)
            if model:
                try:
                    response = await self.chat_callable(
                        messages=[{"role": "user", "content": prompt}],
                        model=model,
                        temperature=0.05,
                        max_tokens=256,
                        job_class=journal_job_class,
                        reasoning_effort=self.reasoning_effort,
                    )
                    content = self.strip_think(response.content)
                    if content and not is_low_signal_journal_body(content):
                        self.journal.write_entry(
                            content=self.format_journal_entry(digest, content),
                            session_keys=[session.key],
                            tags=["session", "synthesis"],
                        )
                        logger.info("Journal synthesized (LLM): {}", session.key)
                        return
                except Exception as exc:
                    logger.warning("Journal LLM failed, using fallback: {}", exc)

            fallback = self.format_journal_entry(digest, self.build_fallback_journal_body(digest))
            self.journal.write_entry(
                content=fallback, session_keys=[session.key], tags=["session", "fallback"]
            )
            logger.info("Journal written (fallback): {}", session.key)
        except Exception as exc:
            logger.warning("Journal synthesis failed (non-fatal): {}: {}", session.key, exc)

    async def synthesize_journal_from_messages(
        self,
        messages: list[dict[str, Any]],
        session_key: str,
        journal_job_class: Any,
    ) -> None:
        class _SessionSnapshot:
            def __init__(self, snapshot_messages: list[dict[str, Any]], key: str):
                self.messages = snapshot_messages
                self.key = key

        await self.synthesize_journal(_SessionSnapshot(messages, session_key), journal_job_class)

    @staticmethod
    def iter_strings(obj: Any) -> list[str]:
        values: list[str] = []
        if isinstance(obj, str):
            return [obj]
        if isinstance(obj, dict):
            for value in obj.values():
                values.extend(BackgroundJobManager.iter_strings(value))
        elif isinstance(obj, list):
            for item in obj:
                values.extend(BackgroundJobManager.iter_strings(item))
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
        category = category_map[candidate.type]
        existing = self.memory.read_memory(category)
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

        if self.find_existing_memory_duplicates(candidate):
            return False
        return True

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

                json_start = content.find("{")
                json_end = content.rfind("}") + 1
                if json_start < 0 or json_end <= json_start:
                    return
                data = json.loads(content[json_start:json_end])
                if not isinstance(data, dict):
                    logger.warning(
                        "Distillation response root is not an object: {} ({})",
                        session.key,
                        type(data).__name__,
                    )
                    return

                candidates = data.get("candidates", [])
                validated_count = 0
                for candidate_data in candidates:
                    try:
                        if not isinstance(candidate_data, dict):
                            logger.debug(
                                "Skipping non-dict distillation candidate for {}: {}",
                                session.key,
                                type(candidate_data).__name__,
                            )
                            continue
                        candidate = AtomicCandidate.from_dict(candidate_data)
                        candidate.source_session = session.key
                        errors = candidate.validate()
                        if errors:
                            logger.warning(
                                "Candidate validation failed: {}: {}", candidate.title, errors
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
                        logger.warning("Failed to parse candidate: {}: {}", title, exc)

                if validated_count > 0:
                    logger.info(
                        "Distillation complete: {} candidates from {}", validated_count, session.key
                    )
                else:
                    logger.debug("No valid candidates distilled: {}", session.key)
            except json.JSONDecodeError as exc:
                logger.warning("Distillation response not valid JSON: {}: {}", session.key, exc)
            except Exception as exc:
                logger.warning("Distillation LLM failed: {}: {}", session.key, exc)
        except Exception as exc:
            logger.warning("Distillation failed (non-fatal): {}: {}", session.key, exc)

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

    async def reflect_on_session(self, session: Any) -> None:
        await self.reflection_service.reflect_on_session(
            messages=session.messages,
            session_key=session.key,
            digest=self.build_session_digest(session.messages, session.key),
        )

    async def reflect_on_session_from_messages(
        self, messages: list[dict[str, Any]], session_key: str
    ) -> None:
        await self.reflection_service.reflect_on_session(
            messages=messages,
            session_key=session_key,
            digest=self.build_session_digest(messages, session_key),
        )

    def save_turn(
        self,
        session: Any,
        messages: list[dict[str, Any]],
        skip: int,
        update_session_timer: Callable[[str], None],
    ) -> None:
        for message in messages[skip:]:
            entry = {k: v for k, v in message.items() if k != "reasoning_content"}
            if entry.get("role") == "tool" and isinstance(entry.get("content"), str):
                content = entry["content"]
                if len(content) > self._TOOL_RESULT_MAX_CHARS:
                    entry["content"] = content[: self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now(timezone.utc)
        update_session_timer(session.key)
