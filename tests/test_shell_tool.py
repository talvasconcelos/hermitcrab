from __future__ import annotations

from hermitcrab.agent.tools.shell import ExecTool


def test_shell_redirection_is_workspace_write_not_destructive() -> None:
    command = "cat > session-2-notes.md <<'EOF'\nnotes\nEOF"

    assert ExecTool._classify_command_risk(command) == "workspace_write"


def test_mv_is_workspace_write_not_destructive() -> None:
    assert ExecTool._classify_command_risk("mv draft.md final.md") == "workspace_write"


def test_rm_still_requires_destructive_approval() -> None:
    assert ExecTool._classify_command_risk("rm draft.md") == "destructive"


def test_destructive_git_still_requires_approval() -> None:
    assert ExecTool._classify_command_risk("git reset --hard HEAD") == "destructive"
