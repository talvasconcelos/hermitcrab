"""Focused tests for shared tool-call recovery helpers."""

from hermitcrab.agent.tool_call_recovery import coerce_inline_tool_calls, normalize_tool_calls
from hermitcrab.providers.base import ToolCallRequest


def test_coerce_inline_tool_calls_recovers_json_with_prefix() -> None:
    content, tool_calls = coerce_inline_tool_calls(
        'Let me check. {"name":"read_memory","arguments":{"category":"facts"}}',
        lambda name: name == "read_memory",
    )

    assert content == "Let me check."
    assert tool_calls == [
        ToolCallRequest(
            id="inline_call_1",
            name="read_memory",
            arguments={"category": "facts"},
        )
    ]


def test_coerce_inline_tool_calls_recovers_xml_payload() -> None:
    content, tool_calls = coerce_inline_tool_calls(
        (
            "Working on it.\n"
            '<minimax:tool_call><invoke name="read_memory">'
            '<parameter name="category">facts</parameter>'
            "</invoke></minimax:tool_call>"
        ),
        lambda name: name == "read_memory",
    )

    assert content == "Working on it."
    assert tool_calls == [
        ToolCallRequest(
            id="inline_xml_call_1",
            name="read_memory",
            arguments={"category": "facts"},
        )
    ]


def test_normalize_tool_calls_repairs_string_arguments() -> None:
    normalized = normalize_tool_calls(
        [ToolCallRequest(id="1", name="read_memory", arguments='{"category":"facts"}')]
    )

    assert normalized == [
        ToolCallRequest(id="1", name="read_memory", arguments={"category": "facts"})
    ]
