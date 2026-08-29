from __future__ import annotations

from pathlib import Path

import pytest

from hermitcrab.agent.tools.shell import ExecTool


def test_shell_redirection_requires_approval() -> None:
    command = "cat > session-2-notes.md <<'EOF'\nnotes\nEOF"

    assert ExecTool._classify_command_risk(command) == "destructive"


def test_shell_metacharacter_chains_require_approval() -> None:
    assert ExecTool._classify_command_risk("echo ok;rm -rf victim") == "destructive"
    assert ExecTool._classify_command_risk("echo ok|sh") == "destructive"
    assert ExecTool._classify_command_risk("printf x&&sudo true") == "destructive"
    assert ExecTool._classify_command_risk("git status;rm victim") == "destructive"
    assert ExecTool._classify_command_risk("echo eA==|base64 -d|sh") == "destructive"


def test_mv_is_workspace_write_not_destructive() -> None:
    assert ExecTool._classify_command_risk("mv draft.md final.md") == "workspace_write"


def test_rm_still_requires_destructive_approval() -> None:
    assert ExecTool._classify_command_risk("rm draft.md") == "destructive"


def test_restricted_shell_allows_workspace_scoped_delete_without_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    assert tool._guard_command("rm draft.md", str(workspace)) is None


def test_restricted_shell_blocks_delete_of_workspace_root(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    assert tool._guard_command("rm -rf .", str(workspace)) == (
        "Error: Command blocked by safety guard (destructive command requires explicit approval)"
    )


def test_restricted_shell_blocks_workspace_delete_with_globs(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    assert tool._guard_command("rm *.tmp", str(workspace)) == (
        "Error: Command blocked by safety guard (destructive command requires explicit approval)"
    )


def test_restricted_shell_blocks_delete_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    sibling = tmp_path / "identities" / "bob" / "draft.md"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    assert tool._guard_command(f"rm {sibling}", str(workspace)) == (
        "Error: Command blocked by safety guard (destructive command requires explicit approval)"
    )


def test_destructive_git_still_requires_approval() -> None:
    assert ExecTool._classify_command_risk("git reset --hard HEAD") == "destructive"


def test_sudo_requires_destructive_approval() -> None:
    assert ExecTool._classify_command_risk("sudo rm -rf /") == "destructive"


def test_interpreter_wrapper_requires_destructive_approval() -> None:
    assert ExecTool._classify_command_risk('bash -c "rm -rf /"') == "destructive"
    assert ExecTool._classify_command_risk('sh -c "cat /etc/passwd"') == "destructive"
    assert ExecTool._classify_command_risk("python -c 'import os'") == "destructive"
    assert ExecTool._classify_command_risk("node -e '1'") == "destructive"


def test_pipe_into_interpreter_requires_destructive_approval() -> None:
    assert ExecTool._classify_command_risk("echo c2ggL2V0Yy9wYXNzd2Q= | base64 -d | sh") == "destructive"


def test_restricted_shell_blocks_quoted_home_path(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    # `$` is shell syntax → requires approval, even inside quotes.
    assert tool._guard_command('cat "$HOME/.ssh/id_rsa"', str(workspace)) == (
        "Error: Command blocked by safety guard (destructive command requires explicit approval)"
    )
    # Approval does not bypass workspace containment.
    assert tool._guard_command(
        'cat "$HOME/.ssh/id_rsa"', str(workspace), destructive_approved=True
    ) == "Error: Command blocked by safety guard (home path outside working dir)"


def test_restricted_shell_blocks_absolute_sibling_identity_path(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    sibling = tmp_path / "identities" / "bob" / "memory.md"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    result = tool._guard_command(f"cat {sibling}", str(workspace))

    assert result == "Error: Command blocked by safety guard (path outside working dir)"


def test_restricted_shell_blocks_home_expansion_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    # `~` and `$HOME` are shell syntax → require approval without bypassing containment.
    assert tool._guard_command("cat ~/bob/memory.md", str(workspace)) == (
        "Error: Command blocked by safety guard (destructive command requires explicit approval)"
    )
    assert tool._guard_command("cat $HOME/bob/memory.md", str(workspace)) == (
        "Error: Command blocked by safety guard (destructive command requires explicit approval)"
    )
    assert tool._guard_command("cat ~/bob/memory.md", str(workspace), destructive_approved=True) == (
        "Error: Command blocked by safety guard (home path outside working dir)"
    )


@pytest.mark.asyncio
async def test_execute_runs_simple_command_via_argv() -> None:
    tool = ExecTool()

    assert await tool.execute("printf ok") == "ok"


@pytest.mark.asyncio
async def test_execute_blocks_shell_syntax_without_approval() -> None:
    tool = ExecTool()

    result = await tool.execute("echo ok; echo bad")

    assert "requires explicit approval" in result


@pytest.mark.asyncio
async def test_execute_approved_shell_syntax_runs_via_shell() -> None:
    tool = ExecTool()
    tool.allow_destructive_command("echo ok; echo bad")

    result = await tool.execute("echo ok; echo bad")

    assert "ok" in result and "bad" in result


def test_restricted_shell_allows_paths_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    inside = workspace / "notes.md"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    assert tool._guard_command(f"cat {inside}", str(workspace)) is None


def test_restricted_shell_blocks_requested_working_dir_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    sibling = tmp_path / "identities" / "bob"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    result = tool._guard_command("cat memory.md", str(sibling))

    assert result == "Error: Command blocked by safety guard (working dir outside workspace)"


def test_restricted_shell_allows_requested_working_dir_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    child = workspace / "project"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    assert tool._guard_command("cat notes.md", str(child)) is None


def test_exec_description_is_dynamic_for_restricted_mode(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    assert "best-effort workspace path checks" in tool.description
    assert "not a sandbox" in tool.description


def test_exec_description_is_dynamic_for_unrestricted_mode(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=False)

    assert "full system access" in tool.description
    assert "dangerous" in tool.description
