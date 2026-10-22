"""Regressions for completing stored tasks without creating done copies."""

from unittest.mock import MagicMock

import pytest

from hermitcrab.agent.loop import AgentLoop
from hermitcrab.agent.memory import MemoryStore
from hermitcrab.agent.tools.memory import (
    ReadMemoryTool,
    SearchMemoryTool,
    UpdateTaskStatusTool,
    WriteTaskTool,
)
from hermitcrab.agent.tools.policy import ToolPermissionLevel, ToolPermissionPolicy
from hermitcrab.agent.tools.registry import ToolRegistry
from hermitcrab.bus.queue import MessageBus


async def test_task_status_update_preserves_original_task_across_reload(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    task = store.write_task("School trip prep", "Bring snacks and water.", "tal")
    other = store.write_task("School trip prep", "Pack a raincoat.", "tal")
    original_path = task.file_path
    original_created_at = store.read_memory("tasks", id=task.id)[0].created_at
    tool = UpdateTaskStatusTool(store)

    for _ in range(2):
        result = await tool.execute(task.id, "done")
        assert result.startswith("Task updated:")
        assert "status=done" in result

    reloaded = MemoryStore(tmp_path)
    tasks = reloaded.read_memory("tasks")
    assert len(tasks) == 2
    updated = reloaded.read_memory("tasks", id=task.id)[0]
    assert updated.metadata["status"] == "done"
    assert updated.content == task.content
    assert updated.created_at == original_created_at
    assert updated.file_path == original_path
    assert reloaded.read_memory("tasks", id=other.id)[0].metadata["status"] == "open"

    for text in (
        await ReadMemoryTool(reloaded).execute("tasks", id=task.id),
        await SearchMemoryTool(reloaded).execute("School trip prep"),
    ):
        assert "Status: done | Assignee: tal" in text
        assert task.id in text
    assert "Status: open" in await ReadMemoryTool(reloaded).execute("tasks", id=other.id)


async def test_task_update_rejects_missing_id_and_invalid_status_without_writes(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    task = store.write_task("Call", "Call tomorrow.", "tal")
    before = task.file_path.read_bytes()
    registry = ToolRegistry()
    registry.register(UpdateTaskStatusTool(store))

    for arguments in (
        {"task_id": "unknown", "status": "done"},
        {"task_id": task.id, "status": "deleted"},
        {"status": "done"},
    ):
        assert (await registry.execute("update_task_status", arguments)).startswith("Error")
    assert task.file_path.read_bytes() == before
    assert len(store.read_memory("tasks")) == 1


def test_task_update_is_registered_as_coordinator_owned_workspace_write(tmp_path) -> None:
    loop = AgentLoop(bus=MessageBus(), provider=MagicMock(), workspace=tmp_path)

    assert loop.tools.has("update_task_status")
    assert any(
        definition["function"]["name"] == "update_task_status"
        for definition in loop.tools.get_definitions()
    )
    metadata = loop.tools.get_metadata("update_task_status")
    assert metadata.permission_level == ToolPermissionLevel.WORKSPACE_WRITE
    assert not metadata.available_to_subagents
    assert ToolPermissionPolicy(allow_subagent_tools_only=True).check("update_task_status", metadata)


@pytest.mark.parametrize("status", ["open", "in_progress", "done", "deferred"])
async def test_task_update_accepts_each_supported_status(tmp_path, status) -> None:
    store = MemoryStore(tmp_path)
    task = store.write_task("Task", "Do it.", "tal")

    assert (await UpdateTaskStatusTool(store).execute(task.id, status)).startswith("Task updated:")
    assert store.read_memory("tasks", id=task.id)[0].metadata["status"] == status


async def test_write_task_cannot_complete_tasks_by_creating_reworded_copies(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    tool = WriteTaskTool(store)
    assert (await tool.execute("Call", "Call tomorrow.", "tal")).startswith("Task saved:")
    original = store.read_memory("tasks")[0]
    result = await tool.execute("Call", "Call tomorrow. Completed.", "tal", status="done")

    assert result.startswith("Error")
    assert "update_task_status" in result
    assert len(store.read_memory("tasks")) == 1
    assert store.read_memory("tasks")[0].metadata["status"] == "open"
    assert store.read_memory("tasks")[0].id == original.id
