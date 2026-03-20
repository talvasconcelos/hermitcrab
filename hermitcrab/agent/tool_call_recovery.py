"""Shared helpers for repairing malformed or inline tool calls."""

from __future__ import annotations

import re
from typing import Callable

import json_repair

from hermitcrab.providers.base import ToolCallRequest

ToolLookup = Callable[[str], bool]


def normalize_tool_calls(tool_calls: list[ToolCallRequest]) -> list[ToolCallRequest]:
    """Repair provider quirks where tool arguments arrive as JSON strings."""
    normalized: list[ToolCallRequest] = []
    for tool_call in tool_calls:
        arguments = tool_call.arguments
        if isinstance(arguments, str):
            try:
                arguments = json_repair.loads(arguments)
            except Exception:
                pass
        normalized.append(
            ToolCallRequest(id=tool_call.id, name=tool_call.name, arguments=arguments)
        )
    return normalized


def coerce_inline_tool_calls(
    content: str | None,
    has_tool: ToolLookup,
) -> tuple[str | None, list[ToolCallRequest]]:
    """Recover inline JSON or XML-like tool calls from assistant text."""
    if not content or not isinstance(content, str):
        return content, []

    text = content.strip()
    starts = [idx for idx, ch in enumerate(text) if ch in "{["]

    for start in reversed(starts):
        prefix = text[:start].rstrip()
        candidate = text[start:].strip()
        try:
            payload = json_repair.loads(candidate)
        except Exception:
            continue

        entries = payload if isinstance(payload, list) else [payload]
        recovered: list[ToolCallRequest] = []
        for idx, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                recovered = []
                break

            name = entry.get("name")
            arguments = entry.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json_repair.loads(arguments)
                except Exception:
                    recovered = []
                    break

            if not isinstance(name, str) or not isinstance(arguments, dict) or not has_tool(name):
                recovered = []
                break

            recovered.append(
                ToolCallRequest(id=f"inline_call_{idx}", name=name, arguments=arguments)
            )

        if recovered:
            return prefix or None, recovered

    xml_match = re.search(
        r"<(?:[\w.-]+:)?tool_call>\s*(.*?)\s*</(?:[\w.-]+:)?tool_call>",
        text,
        re.DOTALL,
    )
    if xml_match:
        prefix = text[: xml_match.start()].rstrip()
        recovered = parse_xml_tool_calls(xml_match.group(1), has_tool)
        if recovered:
            return prefix or None, recovered

    return content, []


def parse_xml_tool_calls(body: str, has_tool: ToolLookup) -> list[ToolCallRequest]:
    """Recover XML-like inline tool calls from assistant text."""
    recovered: list[ToolCallRequest] = []
    invoke_pattern = re.compile(r'<invoke\s+name="([^"]+)">\s*(.*?)\s*</invoke>', re.DOTALL)
    param_pattern = re.compile(
        r'<parameter\s+name="([^"]+)">(.*?)</parameter>',
        re.DOTALL,
    )

    for idx, match in enumerate(invoke_pattern.finditer(body), start=1):
        name = match.group(1).strip()
        if not has_tool(name):
            return []

        arguments: dict[str, str] = {}
        for param_name, raw_value in param_pattern.findall(match.group(2)):
            arguments[param_name.strip()] = raw_value.strip()

        recovered.append(
            ToolCallRequest(id=f"inline_xml_call_{idx}", name=name, arguments=arguments)
        )

    return recovered
