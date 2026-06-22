"""Focused regressions for identity-root runtime isolation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermitcrab.agent.loop import AgentLoop, JobClass
from hermitcrab.agent.turn_runner import TurnOutcome, TurnResult
from hermitcrab.bus.events import InboundMessage
from hermitcrab.bus.queue import MessageBus
from hermitcrab.cli.commands import _build_agent_loop_kwargs
from hermitcrab.config.schema import Config


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return provider


def test_agent_loop_accepts_identity_metadata_and_roots_runtime_state(tmp_path) -> None:
    identity_root = tmp_path / "identities" / "alice"
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path / "workspace-argument",
        identity_name="alice",
        identity_root=identity_root,
        system_root=tmp_path / "system",
    )

    assert loop.identity_name == "alice"
    assert loop.identity_root == identity_root
    assert loop.workspace == identity_root
    assert loop.sessions.workspace == identity_root
    assert loop.memory.workspace == identity_root
    assert loop.knowledge.workspace == identity_root
    assert loop.lists.workspace == identity_root
    assert loop.people.workspace == identity_root
    assert loop.reminders.workspace == identity_root
    assert loop.context.workspace == identity_root
    assert loop.context.system_root == tmp_path / "system"
    assert loop.context.skills.workspace_skills == identity_root / "skills"
    assert loop.subagents.workspace == identity_root


def test_identity_roots_isolate_sessions_memory_lists_people_skills_and_scratchpads(
    tmp_path,
) -> None:
    alice_root = tmp_path / "identities" / "alice"
    bob_root = tmp_path / "identities" / "bob"
    alice = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=alice_root,
        identity_name="alice",
        identity_root=alice_root,
    )
    bob = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=bob_root,
        identity_name="bob",
        identity_root=bob_root,
    )

    session = alice.sessions.get_or_create("cli:shared")
    session.add_message("user", "alice-only session")
    alice.sessions.save(session)
    alice.memory.write_fact("Alice Fact", "Alice memory only.")
    alice.lists.add_items("Alice List", ["one"])
    alice.people.upsert_profile(name="Alice", role="owner")
    alice._ensure_scratchpad("cli:shared")
    (alice_root / "skills" / "alice-skill").mkdir(parents=True)
    (alice_root / "skills" / "alice-skill" / "SKILL.md").write_text(
        "---\nname: alice-skill\ndescription: Alice only.\n---\n",
        encoding="utf-8",
    )

    assert (alice_root / "sessions" / "sessions.sqlite3").exists()
    assert alice.sessions.search_history("alice-only session")
    assert list((alice_root / "memory" / "facts").glob("*.md"))
    assert (alice_root / "lists" / "alice-list.md").exists()
    assert (alice_root / "people" / "profiles" / "alice.md").exists()
    assert (alice_root / "scratchpads" / "cli_shared.md").exists()
    assert any(skill["name"] == "alice-skill" for skill in alice.context.skills.list_skills())

    assert not bob.sessions.search_history("alice-only session")
    assert not list((bob_root / "memory" / "facts").glob("*.md"))
    assert not (bob_root / "lists" / "alice-list.md").exists()
    assert not (bob_root / "people" / "profiles" / "alice.md").exists()
    assert not (bob_root / "scratchpads" / "cli_shared.md").exists()
    assert all(skill["name"] != "alice-skill" for skill in bob.context.skills.list_skills())


def test_agent_loop_kwargs_include_owner_identity_metadata(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path), "identities": {"ownerIdentity": "tal"}})

    kwargs = _build_agent_loop_kwargs(config, _provider())

    assert kwargs["identity_name"] == "tal"
    assert kwargs["identity_root"] == tmp_path / "identities" / "tal"
    assert kwargs["workspace"] == kwargs["identity_root"]
    assert kwargs["system_root"] == tmp_path / "system"
    assert kwargs["restrict_to_workspace"] is True


def test_cli_identities_are_bounded_even_if_legacy_config_disables_workspace_restriction(
    tmp_path,
) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "tools": {"restrictToWorkspace": False},
            "identities": {"registry": {"owner": {}, "alice": {}}},
        }
    )

    owner_kwargs = _build_agent_loop_kwargs(config, _provider(), identity_name="owner")
    alice_kwargs = _build_agent_loop_kwargs(config, _provider(), identity_name="alice")

    assert owner_kwargs["restrict_to_workspace"] is True
    assert alice_kwargs["restrict_to_workspace"] is True


@pytest.mark.asyncio
async def test_identity_file_tools_deny_listing_outside_identity_root_by_default(tmp_path) -> None:
    alice_root = tmp_path / "identities" / "alice"
    bob_root = tmp_path / "identities" / "bob"
    alice_root.mkdir(parents=True)
    bob_root.mkdir(parents=True)
    (bob_root / "secret.md").write_text("bob only", encoding="utf-8")

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=alice_root,
        identity_name="alice",
        identity_root=alice_root,
    )

    result = await loop.tools.execute("list_dir", {"path": str(bob_root)})

    assert "outside allowed directory" in result


@pytest.mark.asyncio
async def test_identity_exec_is_available_when_restricted_by_default(
    tmp_path,
) -> None:
    alice_root = tmp_path / "identities" / "alice"
    alice_root.mkdir(parents=True)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=alice_root,
        identity_name="alice",
        identity_root=alice_root,
    )

    assert loop.tools.has("exec")
    exec_tool = loop.tools.get("exec")
    assert exec_tool is not None
    assert "best-effort workspace path checks" in exec_tool.description
    assert "not a sandbox" in exec_tool.description

    result = await loop.tools.execute("exec", {"command": "pwd"})
    assert str(alice_root) in result


@pytest.mark.asyncio
async def test_identity_exec_uses_unrestricted_mode_when_workspace_restriction_is_disabled(
    tmp_path,
) -> None:
    alice_root = tmp_path / "identities" / "alice"
    alice_root.mkdir(parents=True)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=alice_root,
        identity_name="alice",
        identity_root=alice_root,
        restrict_to_workspace=False,
    )

    assert loop.tools.has("exec")
    exec_tool = loop.tools.get("exec")
    assert exec_tool is not None
    assert "full system access" in exec_tool.description
    assert "dangerous" in exec_tool.description


@pytest.mark.asyncio
async def test_capabilities_command_reports_restricted_exec_and_core_status(tmp_path) -> None:
    alice_root = tmp_path / "identities" / "alice"
    alice_root.mkdir(parents=True)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=alice_root,
        identity_name="alice",
        identity_root=alice_root,
    )
    session = loop.sessions.get_or_create("cli:direct")
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/capabilities")

    response = await loop._maybe_handle_slash_command(msg, "cli:direct", session)

    assert response is not None
    assert "workspace_restriction: on" in response.content
    assert "exec: enabled" in response.content
    assert "best-effort workspace path checks" in response.content
    assert "spawn: enabled" in response.content
    assert "memory: enabled" in response.content
    assert "tool_hints: available if channel config enables them" in response.content


@pytest.mark.asyncio
async def test_tools_alias_maps_to_capabilities_output(tmp_path) -> None:
    alice_root = tmp_path / "identities" / "alice"
    alice_root.mkdir(parents=True)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=alice_root,
        identity_name="alice",
        identity_root=alice_root,
    )
    session = loop.sessions.get_or_create("cli:direct")
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/tools")

    response = await loop._maybe_handle_slash_command(msg, "cli:direct", session)

    assert response is not None
    assert response.content.startswith("capabilities:\n")


def test_agent_loop_kwargs_apply_identity_model_overrides(tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {
                "global": {"model": "openai-codex/gpt-5.4-mini"},
                "paula-model": {"model": "ollama/granite4"},
            },
            "agents": {
                "defaults": {
                    "model": "ollama/gemma4",
                    "jobModels": {
                        "interactiveResponse": "global",
                        "reflection": "global",
                    },
                },
            },
            "identities": {
                "registry": {
                    "owner": {},
                    "paula": {"models": {"interactiveResponse": "paula-model"}},
                }
            },
        }
    )

    owner_kwargs = _build_agent_loop_kwargs(config, _provider(), identity_name="owner")
    paula_kwargs = _build_agent_loop_kwargs(config, _provider(), identity_name="paula")

    assert owner_kwargs["job_models"][JobClass.INTERACTIVE_RESPONSE] == "global"
    assert paula_kwargs["job_models"][JobClass.INTERACTIVE_RESPONSE] == "paula-model"
    assert paula_kwargs["job_models"][JobClass.REFLECTION] == "global"


@pytest.mark.asyncio
async def test_interactive_turn_passes_named_model_ref_to_provider_path(tmp_path, monkeypatch) -> None:
    captured: dict[str, str | None] = {}
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path / "identities" / "paula",
        identity_name="paula",
        identity_root=tmp_path / "identities" / "paula",
        job_models={JobClass.INTERACTIVE_RESPONSE: "granite4"},
        named_models={"granite4": Config.model_validate({"models": {"granite4": {"model": "ollama/granite4:350m"}}}).models["granite4"]},
    )

    async def fake_run_agent_loop(*args, **kwargs):
        captured["model_override"] = kwargs.get("model_override")
        return TurnResult(
            final_content="ok",
            tools_used=[],
            messages=[],
            outcome=TurnOutcome.COMPLETED,
        )

    monkeypatch.setattr(loop, "_run_agent_loop", fake_run_agent_loop)
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="hello")

    await loop._process_message(msg, session_key="cli:direct")

    assert captured["model_override"] == "granite4"
