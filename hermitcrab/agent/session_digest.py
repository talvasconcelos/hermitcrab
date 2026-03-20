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
                continue

            if role == "assistant":
                self._digest_assistant_message(msg, content, event_lines, wikilinks)
                continue

            if role == "tool":
                self._digest_tool_message(msg, content, event_lines, outcomes, failures)

        unique_links = list(dict.fromkeys(wikilinks))
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

    def _digest_assistant_message(
        self,
        msg: dict[str, Any],
        content: str,
        event_lines: list[str],
        wikilinks: list[str],
    ) -> None:
        tool_calls = msg.get("tool_calls") if isinstance(msg.get("tool_calls"), list) else []
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
            if title and tool_name in {"write_task", "write_goal", "write_decision", "write_fact"}:
                event_lines.append(f"- Assistant saved {tool_name[6:]} [[{title}]].")
                continue
            focus = title or clean_snippet(arguments.get("query") or arguments.get("path"))
            if focus:
                event_lines.append(f"- Assistant used {tool_name}: {focus}")

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
        elif lowered.startswith(("task saved:", "goal saved:", "decision saved:", "fact saved:")):
            outcomes.append(content)

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
