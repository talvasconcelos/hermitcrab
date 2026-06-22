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
    (tmp_path / "USER.md").write_text("# User\n", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("# Soul\n", encoding="utf-8")
    (tmp_path / "IDENTITY.md").write_text("# Identity\n", encoding="utf-8")


def onboarding_audit_events(tmp_path) -> list[dict]:
    audit_path = tmp_path / "onboarding" / "audit.jsonl"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def make_service_with_content(tmp_path, content: str):
    async def chat_callable(**_kwargs):
        return FakeResponse(content=content)

    return OnboardingProfileService(
        tmp_path,
        chat_callable=chat_callable,
        model="test-model",
    )


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


@pytest.mark.asyncio
async def test_onboarding_writes_grounded_insight_and_audit(tmp_path):
    enable_onboarding_workspace(tmp_path)
    service = make_service_with_content(
        tmp_path,
        json.dumps(
            {
                "skip": False,
                "confidence": 0.9,
                "insights": [
                    {
                        "target": "USER.md",
                        "bullet": "- User prefers concise replies unless they ask for detail.",
                        "evidence": "Keep it concise unless I ask for detail.",
                        "confidence": 0.9,
                        "reason": "durable communication preference",
                    }
                ],
                "observed_domains": ["communication"],
                "pending_assumptions": [],
            }
        ),
    )

    changed = await service.maybe_sync_from_messages(
        [{"role": "user", "content": "Keep it concise unless I ask for detail."}]
    )

    assert changed is True
    user_md = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert "User prefers concise replies" in user_md
    events = onboarding_audit_events(tmp_path)
    assert events[-1]["event"] == "written"
    assert events[-1]["target"] == "USER.md"
    assert events[-1]["evidence"] == "Keep it concise unless I ask for detail."


@pytest.mark.asyncio
async def test_onboarding_rejects_ungrounded_or_missing_evidence(tmp_path):
    enable_onboarding_workspace(tmp_path)
    service = make_service_with_content(
        tmp_path,
        json.dumps(
            {
                "skip": False,
                "confidence": 0.95,
                "insights": [
                    {
                        "target": "USER.md",
                        "bullet": "- User loves Kubernetes for every project.",
                        "evidence": "Kubernetes is mandatory.",
                        "confidence": 0.95,
                        "reason": "project preference",
                    },
                    {
                        "target": "IDENTITY.md",
                        "bullet": "- Keep replies concise.",
                        "confidence": 0.95,
                        "reason": "communication style",
                    },
                ],
            }
        ),
    )

    changed = await service.maybe_sync_from_messages(
        [{"role": "user", "content": "Keep it concise unless I ask for detail."}]
    )

    assert changed is False
    assert "Kubernetes" not in (tmp_path / "USER.md").read_text(encoding="utf-8")
    events = onboarding_audit_events(tmp_path)
    assert [event["reason"] for event in events[-2:]] == [
        "evidence_not_in_conversation",
        "missing_evidence",
    ]


@pytest.mark.asyncio
async def test_onboarding_duplicate_insight_is_audited_not_rewritten(tmp_path):
    enable_onboarding_workspace(tmp_path)
    payload = json.dumps(
        {
            "skip": False,
            "confidence": 0.9,
            "insights": [
                {
                    "target": "USER.md",
                    "bullet": "- User prefers concise replies unless they ask for detail.",
                    "evidence": "Keep it concise unless I ask for detail.",
                    "confidence": 0.9,
                    "reason": "durable communication preference",
                }
            ],
        }
    )
    service = make_service_with_content(tmp_path, payload)
    messages = [{"role": "user", "content": "Keep it concise unless I ask for detail."}]

    assert await service.maybe_sync_from_messages(messages) is True
    assert await service.maybe_sync_from_messages(messages) is False

    user_md = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert user_md.count("User prefers concise replies") == 1
    assert onboarding_audit_events(tmp_path)[-1]["event"] == "duplicate"
