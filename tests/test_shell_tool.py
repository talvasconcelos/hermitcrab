from __future__ import annotations

from pathlib import Path

from hermitcrab.agent.tools.shell import ExecTool


def test_shell_redirection_is_workspace_write_not_destructive() -> None:
    command = "cat > session-2-notes.md <<'EOF'\nnotes\nEOF"

    assert ExecTool._classify_command_risk(command) == "workspace_write"


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


def test_restricted_shell_blocks_absolute_sibling_identity_path(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    sibling = tmp_path / "identities" / "bob" / "memory.md"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    result = tool._guard_command(f"cat {sibling}", str(workspace))

    assert result == "Error: Command blocked by safety guard (path outside working dir)"


def test_restricted_shell_blocks_home_expansion_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "identities" / "alice"
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)

    assert tool._guard_command("cat ~/bob/memory.md", str(workspace)) == (
        "Error: Command blocked by safety guard (home path outside working dir)"
    )
    assert tool._guard_command("cat $HOME/bob/memory.md", str(workspace)) == (
        "Error: Command blocked by safety guard (home path outside working dir)"
    )


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
