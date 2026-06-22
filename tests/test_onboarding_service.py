from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from hermitcrab.agent.context import ContextBuilder
from hermitcrab.agent.onboarding import OnboardingProfileService


@dataclass
class FakeResponse:
    content: str


async def fake_chat_callable(**_kwargs):
    return FakeResponse(content='{"skip": true, "confidence": 0.0, "user_md": [], "soul_md": [], "identity_md": []}')


def make_service(tmp_path):
    return OnboardingProfileService(
        tmp_path,
        chat_callable=fake_chat_callable,
        model="test-model",
    )


def enable_onboarding_workspace(tmp_path):
    (tmp_path / ".onboarding_mode").write_text("enabled\n", encoding="utf-8")
    (tmp_path / "ONBOARDING_MODE.md").write_text("onboarding prompt\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    (tmp_path / "TOOLS.md").write_text("tools\n", encoding="utf-8")


def test_onboarding_state_initializes_active_when_flag_exists(tmp_path):
    enable_onboarding_workspace(tmp_path)
    service = make_service(tmp_path)

    assert service.is_enabled() is True

    state_path = tmp_path / "onboarding" / "state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "active"
    assert state["started_at"]
    assert state["last_updated_at"]


def test_onboarding_state_pause_resume_complete_controls_enabled(tmp_path):
    enable_onboarding_workspace(tmp_path)
    service = make_service(tmp_path)

    service.pause()
    assert service.is_enabled() is False

    service.resume()
    assert service.is_enabled() is True

    service.complete()
    assert service.is_enabled() is False
    state = json.loads((tmp_path / "onboarding" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"


def test_onboarding_corrupt_state_falls_back_to_active_when_flag_exists(tmp_path):
    enable_onboarding_workspace(tmp_path)
    state_path = tmp_path / "onboarding" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("not json", encoding="utf-8")
    service = make_service(tmp_path)

    assert service.is_enabled() is True
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "active"


def test_context_builder_omits_onboarding_prompt_when_state_paused_or_completed(tmp_path):
    enable_onboarding_workspace(tmp_path)
    service = make_service(tmp_path)
    builder = ContextBuilder(tmp_path)

    assert builder._load_onboarding_prompt() == "onboarding prompt"

    service.pause()
    assert builder._load_onboarding_prompt() == ""

    service.resume()
    assert builder._load_onboarding_prompt() == "onboarding prompt"

    service.complete()
    assert builder._load_onboarding_prompt() == ""


def test_context_builder_omits_onboarding_prompt_without_flag(tmp_path):
    (tmp_path / "ONBOARDING_MODE.md").write_text("onboarding prompt\n", encoding="utf-8")
    builder = ContextBuilder(tmp_path)

    assert builder._load_onboarding_prompt() == ""
