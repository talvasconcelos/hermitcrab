"""Deterministic session-digest helpers for background cognition."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import json_repair

from hermitcrab.agent.message_preparation import (
    clean_snippet,
    extract_subagent_task,
    is_subagent_completion_prompt,
    is_transition_assistant_message,
    parse_subagent_completion_prompt,
)


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
    user_goal: str
    artifacts_changed: list[str]
    decisions_made: list[str]
    open_loops: list[str]
    assistant_responses: list[str]
    signals: dict[str, int]


class SessionDigestBuilder:
    """Build and format background-cognition digests."""

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
        artifacts_changed: list[str] = []
        decisions_made: list[str] = []
        assistant_responses: list[str] = []
        seen_assistant_or_tool = False
        tool_turn_count = 0

        for msg in messages[-40:]:
            role = msg.get("role")
            content = clean_snippet(msg.get("content"))
            if role == "user":
                if not content:
                    continue
                raw_content = str(msg.get("content") or "")
                if is_subagent_completion_prompt(raw_content):
                    self._digest_subagent_completion(
                        raw_content,
                        event_lines,
                        outcomes,
                        failures,
                        artifacts_changed,
                    )
                    continue
                event_lines.append(f"- User: {content}")
                user_requests.append(content)
                if seen_assistant_or_tool:
                    user_corrections.append(content)
                continue

            if role == "assistant":
                seen_assistant_or_tool = True
                tool_calls = (
                    msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else []
                )
                if (
                    content
                    and not is_transition_assistant_message(content, tool_calls)
                    and self._is_grounded_assistant_content(
                        content,
                        user_requests=user_requests,
                        outcomes=outcomes,
                        artifacts_changed=artifacts_changed,
                    )
                ):
                    assistant_responses.append(content)
                tool_turn_count += self._digest_assistant_message(
                    msg,
                    content,
                    event_lines,
                    user_requests,
                    outcomes,
                    wikilinks,
                    artifacts_changed,
                    decisions_made,
                )
                continue

            if role == "tool":
                seen_assistant_or_tool = True
                self._digest_tool_message(msg, content, event_lines, outcomes, failures)

        unique_links = list(dict.fromkeys(wikilinks))
        unique_artifacts = [item for item in dict.fromkeys(artifacts_changed) if item]
        unique_decisions = [item for item in dict.fromkeys(decisions_made) if item]
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
            user_goal=self._select_user_goal(user_requests),
            artifacts_changed=unique_artifacts[:10],
            decisions_made=unique_decisions[:8],
            open_loops=self._build_open_loops(user_requests, outcomes, failures)[:6],
            assistant_responses=assistant_responses[-6:],
            signals={
                "user_turn_count": len(user_requests),
                "followup_user_turn_count": max(0, len(user_requests) - 1),
                "assistant_tool_turn_count": tool_turn_count,
                "failure_count": len(failures),
                "outcome_count": len(outcomes),
            },
        )

    def _digest_assistant_message(
        self,
        msg: dict[str, Any],
        content: str,
        event_lines: list[str],
        user_requests: list[str],
        outcomes: list[str],
        wikilinks: list[str],
        artifacts_changed: list[str],
        decisions_made: list[str],
    ) -> int:
        tool_calls = msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else []
        if (
            content
            and not is_transition_assistant_message(content, tool_calls)
            and self._is_grounded_assistant_content(
                content,
                user_requests=user_requests,
                outcomes=outcomes,
                artifacts_changed=artifacts_changed,
            )
        ):
            event_lines.append(f"- Assistant: {content}")
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            tool_name = self.extract_tool_name(call)
            arguments = self.extract_tool_arguments(call)
            title = clean_snippet(arguments.get("title"), max_chars=80)
            path = clean_snippet(arguments.get("path"), max_chars=120)
            if title and tool_name.startswith("write_"):
                wikilinks.append(f"[[{title}]]")
                artifacts_changed.append(f"[[{title}]]")
            if title and tool_name in {"write_task", "write_goal", "write_decision", "write_fact"}:
                event_lines.append(f"- Assistant saved {tool_name[6:]} [[{title}]].")
                if tool_name == "write_decision":
                    decisions_made.append(f"[[{title}]]")
                continue
            if path and tool_name in {"write_file", "edit_file", "read_file"}:
                artifacts_changed.append(path)
            focus = title or clean_snippet(arguments.get("query") or arguments.get("path"))
            if focus:
                event_lines.append(f"- Assistant used {tool_name}: {focus}")
        return len(tool_calls)

    @staticmethod
    def _is_grounded_assistant_content(
        content: str,
        *,
        user_requests: list[str],
        outcomes: list[str],
        artifacts_changed: list[str],
    ) -> bool:
        references = [*user_requests[-3:], *outcomes[-3:], *artifacts_changed[-3:]]
        if not references:
            return True

        content_tokens = {
            token
            for token in SessionDigestBuilder._tokenize_grounding_text(content)
            if len(token) >= 4
        }
        if not content_tokens:
            return False

        for reference in references:
            reference_tokens = {
                token
                for token in SessionDigestBuilder._tokenize_grounding_text(reference)
                if len(token) >= 4
            }
            if content_tokens & reference_tokens:
                return True
        return False

    @staticmethod
    def _tokenize_grounding_text(value: str) -> list[str]:
        normalized = "".join(char.lower() if char.isalnum() else " " for char in value)
        return normalized.split()

    @staticmethod
    def _build_open_loops(
        user_requests: list[str], outcomes: list[str], failures: list[str]
    ) -> list[str]:
        open_loops: list[str] = []
        if failures:
            open_loops.append(failures[-1])
        if user_requests and not outcomes:
            open_loops.append(user_requests[-1])
        return open_loops

    @staticmethod
    def _select_user_goal(user_requests: list[str]) -> str:
        """Prefer the session's primary request over late status pings or corrections."""
        for request in user_requests:
            if request and request.strip():
                return request
        return ""

    @staticmethod
    def _digest_tool_message(
        msg: dict[str, Any],
        content: str,
        event_lines: list[str],
        outcomes: list[str],
        failures: list[str],
    ) -> None:
        tool_name = clean_snippet(msg.get("name"), max_chars=60) or "tool"
        if not content:
            return
        lowered = content.lower()
        if lowered.startswith("error") or "tool error" in lowered or "failed" in lowered:
            failure = f"{tool_name}: {content}"
            failures.append(failure)
            event_lines.append(f"- Tool failure ({tool_name}): {content}")
        elif tool_name == "edit_file" or tool_name.startswith("write_"):
            outcomes.append(content)
            event_lines.append(f"- Tool success ({tool_name}): {content}")

    @classmethod
    def _digest_subagent_completion(
        cls,
        raw_content: str,
        event_lines: list[str],
        outcomes: list[str],
        failures: list[str],
        artifacts_changed: list[str],
    ) -> None:
        parsed = parse_subagent_completion_prompt(raw_content)
        if parsed is None:
            task = extract_subagent_task(raw_content)
            if task:
                event_lines.append(f"- Subagent reported back for task: {task}")
                artifacts_changed.append(task)
            return

        label = clean_snippet(parsed.get("label"), max_chars=80) or "Subagent"
        task = clean_snippet(parsed.get("task"), max_chars=180)
        result = clean_snippet(parsed.get("result"), max_chars=220)
        files = parsed.get("files", "").strip()
        status = parsed.get("status", "").strip().lower()
        exit_reason = parsed.get("exit_reason", "").strip().lower()

        if files and files.lower() != "none":
            for path in files.split(","):
                cleaned = clean_snippet(path, max_chars=140)
                if cleaned:
                    artifacts_changed.append(cleaned)

        if status == "failed":
            failure = f"{label} failed"
            if task:
                failure += f" for {task}"
            if result:
                failure += f": {result}"
            failures.append(failure)
            event_lines.append(f"- Subagent failure ({label}): {result or exit_reason or task}")
            return

        if status == "completed partially":
            detail = result or exit_reason or task or label
            failures.append(f"{label} partial result: {detail}")
            event_lines.append(f"- Subagent partial result ({label}): {detail}")
            return

        detail = result or task or label
        outcomes.append(f"{label}: {detail}")
        event_lines.append(f"- Subagent completed ({label}): {detail}")

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
        request = SessionDigestBuilder._sentence_fragment(
            digest.user_goal or "the user's latest request"
        )
        lines = [f"I worked on {request}."]
        if digest.outcomes:
            lines.append(
                f"Main outcome: {SessionDigestBuilder._sentence_fragment(digest.outcomes[-1])}."
            )
        elif digest.assistant_responses:
            lines.append(
                f"Main response: {SessionDigestBuilder._sentence_fragment(digest.assistant_responses[-1])}."
            )
        if digest.artifacts_changed:
            lines.append(
                f"Key artifacts: {SessionDigestBuilder._sentence_fragment(', '.join(digest.artifacts_changed[:4]))}."
            )
        if digest.decisions_made:
            lines.append(
                f"Decisions recorded: {SessionDigestBuilder._sentence_fragment(', '.join(digest.decisions_made[:3]))}."
            )
        if digest.open_loops:
            lines.append(
                f"Still open: {SessionDigestBuilder._sentence_fragment(digest.open_loops[-1])}."
            )
        return " ".join(lines)

    @staticmethod
    def _sentence_fragment(text: str) -> str:
        """Trim trailing punctuation so fallback sentences stay readable."""
        return text.strip().rstrip(".!")
