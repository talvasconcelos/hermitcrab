"""Tests for HeartbeatService."""

import asyncio
from pathlib import Path

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
            from unittest.mock import Mock
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
