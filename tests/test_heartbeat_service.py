"""Tests for HeartbeatService."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest

from hermitcrab.heartbeat.service import HeartbeatService


@pytest.mark.asyncio
async def test_start_is_idempotent(tmp_path: Path) -> None:
    """Test that starting the service twice returns the same task."""
    async def _on_execute(_: str) -> str:
        return "done"

    service = HeartbeatService(
        workspace=tmp_path,
        provider=None,  # type: ignore
        model="test-model",
        on_execute=_on_execute,
        interval_s=9999,
        enabled=True,
    )

    await service.start()
    first_task = service._task
    await service.start()  # Should be no-op

    assert service._task is first_task

    service.stop()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stop_cancels_task(tmp_path: Path) -> None:
    """Test that stop() cancels the background task."""
    async def _on_execute(_: str) -> str:
        return "done"

    service = HeartbeatService(
        workspace=tmp_path,
        provider=None,  # type: ignore
        model="test-model",
        on_execute=_on_execute,
        interval_s=9999,
        enabled=True,
    )

    await service.start()
    assert service._running is True
    assert service._task is not None

    service.stop()
    assert service._running is False
    assert service._task is None


@pytest.mark.asyncio
async def test_disabled_service_does_not_start(tmp_path: Path) -> None:
    """Test that a disabled service does not start."""
    async def _on_execute(_: str) -> str:
        return "done"

    service = HeartbeatService(
        workspace=tmp_path,
        provider=None,  # type: ignore
        model="test-model",
        on_execute=_on_execute,
        interval_s=9999,
        enabled=False,
    )

    await service.start()
    assert service._running is False
    assert service._task is None


@pytest.mark.asyncio
async def test_tick_with_empty_heartbeat_file(tmp_path: Path) -> None:
    """Test that tick handles missing HEARTBEAT.md gracefully."""
    async def _on_execute(_: str) -> str:
        return "done"

    service = HeartbeatService(
        workspace=tmp_path,
        provider=None,  # type: ignore
        model="test-model",
        on_execute=_on_execute,
        interval_s=9999,
        enabled=True,
    )

    # Should not raise
    await service._tick()


@pytest.mark.asyncio
async def test_tick_with_heartbeat_file(tmp_path: Path) -> None:
    """Test that tick processes HEARTBEAT.md content."""
    # Create HEARTBEAT.md
    heartbeat_file = tmp_path / "HEARTBEAT.md"
    heartbeat_file.write_text("# Tasks\n- Task 1\n", encoding="utf-8")

    captured_tasks: list[str] = []

    async def _on_execute(tasks: str) -> str:
        captured_tasks.append(tasks)
        return "completed"

    async def _on_notify(_: str) -> None:
        pass

    # Mock provider
    class MockProvider:
        async def chat(self, **kwargs):
            response = Mock()
            response.has_tool_calls = True
            response.tool_calls = [Mock(arguments={"action": "run", "tasks": "test tasks"})]
            return response

    service = HeartbeatService(
        workspace=tmp_path,
        provider=MockProvider(),  # type: ignore
        model="test-model",
        on_execute=_on_execute,
        on_notify=_on_notify,
        interval_s=9999,
        enabled=True,
    )

    await service._tick()

    assert len(captured_tasks) == 1
    assert captured_tasks[0] == "test tasks"


@pytest.mark.asyncio
async def test_tick_parses_json_string_tool_arguments(tmp_path: Path) -> None:
    """Heartbeat should tolerate providers returning JSON string arguments."""
    heartbeat_file = tmp_path / "HEARTBEAT.md"
    heartbeat_file.write_text("# Tasks\n- Task 1\n", encoding="utf-8")

    captured_tasks: list[str] = []

    async def _on_execute(tasks: str) -> str:
        captured_tasks.append(tasks)
        return "completed"

    class MockProvider:
        async def chat(self, **kwargs):
            response = Mock()
            response.has_tool_calls = True
            response.tool_calls = [Mock(arguments='{"action": "run", "tasks": "json tasks"}')]
            return response

    service = HeartbeatService(
        workspace=tmp_path,
        provider=MockProvider(),  # type: ignore
        model="test-model",
        on_execute=_on_execute,
        interval_s=9999,
        enabled=True,
    )

    await service._tick()

    assert captured_tasks == ["json tasks"]


@pytest.mark.asyncio
async def test_tick_skips_invalid_tool_arguments_without_crashing(tmp_path: Path) -> None:
    """Heartbeat should degrade safely when providers return malformed tool arguments."""
    heartbeat_file = tmp_path / "HEARTBEAT.md"
    heartbeat_file.write_text("# Tasks\n- Task 1\n", encoding="utf-8")

    captured_tasks: list[str] = []

    async def _on_execute(tasks: str) -> str:
        captured_tasks.append(tasks)
        return "completed"

    class MockProvider:
        async def chat(self, **kwargs):
            response = Mock()
            response.has_tool_calls = True
            response.tool_calls = [Mock(arguments='not-json')]
            return response

    service = HeartbeatService(
        workspace=tmp_path,
        provider=MockProvider(),  # type: ignore
        model="test-model",
        on_execute=_on_execute,
        interval_s=9999,
        enabled=True,
    )

    await service._tick()

    assert captured_tasks == []


@pytest.mark.asyncio
async def test_tick_bypasses_llm_with_direct_marker(tmp_path: Path) -> None:
    """Heartbeat direct mode should execute Active Tasks without calling the provider."""
    heartbeat_file = tmp_path / "HEARTBEAT.md"
    heartbeat_file.write_text(
        "\n".join(
            [
                "<!-- HEARTBEAT_DIRECT -->",
                "# Heartbeat Tasks",
                "",
                "## Active Tasks",
                "",
                "- Check inbox",
                "- Review alerts",
                "",
                "## Completed",
            ]
        ),
        encoding="utf-8",
    )

    captured_tasks: list[str] = []

    async def _on_execute(tasks: str) -> str:
        captured_tasks.append(tasks)
        return "completed"

    class FailingProvider:
        async def chat(self, **kwargs):
            raise AssertionError("provider.chat should not be called in direct mode")

    service = HeartbeatService(
        workspace=tmp_path,
        provider=FailingProvider(),  # type: ignore
        model="test-model",
        on_execute=_on_execute,
        interval_s=9999,
        enabled=True,
    )

    await service._tick()

    assert captured_tasks == ["- Check inbox\n- Review alerts"]
