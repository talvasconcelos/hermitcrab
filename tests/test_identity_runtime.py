"""Focused regressions for identity-root runtime isolation."""

from __future__ import annotations

from unittest.mock import MagicMock

from hermitcrab.agent.loop import AgentLoop
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

    assert (alice_root / "sessions" / "cli_shared.jsonl").exists()
    assert list((alice_root / "memory" / "facts").glob("*.md"))
    assert (alice_root / "lists" / "alice-list.md").exists()
    assert (alice_root / "people" / "profiles" / "alice.md").exists()
    assert (alice_root / "scratchpads" / "cli_shared.md").exists()
    assert any(skill["name"] == "alice-skill" for skill in alice.context.skills.list_skills())

    assert not (bob_root / "sessions" / "cli_shared.jsonl").exists()
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
