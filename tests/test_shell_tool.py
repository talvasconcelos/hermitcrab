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


def test_read_only_shell_chains_are_not_classified_destructive() -> None:
    assert ExecTool._classify_command_risk("which nak; nak --version 2>/dev/null") == "read_only"
    assert ExecTool._classify_command_risk("ls -la ~/.hermitcrab/ 2>/dev/null") == "read_only"
    assert ExecTool._classify_command_risk("grep -rl nsec ~/.hermitcrab/ | head -20") == "read_only"


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

    # `$HOME` reads outside the workspace, so containment blocks it directly.
    assert tool._guard_command('cat "$HOME/.ssh/id_rsa"', str(workspace)) == (
        "Error: Command blocked by safety guard (home path outside working dir)"
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

    # `~` and `$HOME` read outside the workspace, so containment blocks them directly.
    assert tool._guard_command("cat ~/bob/memory.md", str(workspace)) == (
        "Error: Command blocked by safety guard (home path outside working dir)"
    )
    assert tool._guard_command("cat $HOME/bob/memory.md", str(workspace)) == (
        "Error: Command blocked by safety guard (home path outside working dir)"
    )
    assert tool._guard_command("cat ~/bob/memory.md", str(workspace), destructive_approved=True) == (
        "Error: Command blocked by safety guard (home path outside working dir)"
    )


@pytest.mark.asyncio
async def test_execute_runs_simple_command_via_argv() -> None:
    tool = ExecTool()

    assert await tool.execute("printf ok") == "ok"


@pytest.mark.asyncio
async def test_execute_blocks_real_shell_features_without_approval() -> None:
    tool = ExecTool()

    result = await tool.execute("echo $(printf hi)")

    assert "shell syntax requires explicit approval" in result


@pytest.mark.asyncio
async def test_execute_read_only_shell_chain_requires_shell_not_destructive_approval() -> None:
    tool = ExecTool()

    result = await tool.execute("which nak; nak --version 2>/dev/null")

    assert "shell syntax requires explicit approval" in result
    assert "destructive" not in result


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


def test_quoted_metacharacters_do_not_require_shell() -> None:
    assert not ExecTool._requires_shell('find . -name "*.py" -o -name "*.md"')
    assert not ExecTool._requires_shell("grep 'a;b' notes.md")
    assert not ExecTool._requires_shell('curl "https://example.com/search?q=a&b"')
    assert not ExecTool._requires_shell(r"echo a\;b")
    assert ExecTool._requires_shell("find . -name '*.py' | head")
    assert ExecTool._requires_shell('echo "$HOME"')
    assert ExecTool._requires_shell("ls *.py")
    assert ExecTool._requires_shell("echo 'unterminated")


@pytest.mark.asyncio
async def test_quoted_glob_command_runs_without_approval(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    tool = ExecTool()

    result = await tool.execute(f'find {tmp_path} -name "*.md"')

    assert "requires explicit approval" not in result
    assert "notes.md" in result


@pytest.mark.asyncio
async def test_approved_command_mismatch_names_the_approved_command(tmp_path: Path) -> None:
    victim = tmp_path / "victim.md"
    victim.write_text("x", encoding="utf-8")
    tool = ExecTool()
    tool.allow_destructive_command(f"rm {victim}")

    result = await tool.execute(f"rm -f {victim}")

    assert "already approved for this turn" in result
    assert f"`rm {victim}`" in result


@pytest.mark.asyncio
async def test_approved_destructive_command_runs_and_consumes_the_approval(tmp_path: Path) -> None:
    victim = tmp_path / "victim.md"
    victim.write_text("x", encoding="utf-8")
    tool = ExecTool()
    tool.allow_destructive_command(f"rm -f {victim}")

    result = await tool.execute(f"rm -f {victim}")

    assert not victim.exists()
    assert "requires explicit approval" not in result


def test_plain_argv_chain_rejects_shell_features() -> None:
    assert ExecTool._parse_plain_argv_chain("echo hi | head -1") is not None
    assert ExecTool._parse_plain_argv_chain("echo $HOME") is None
    assert ExecTool._parse_plain_argv_chain("ls *.py") is None
    assert ExecTool._parse_plain_argv_chain("echo hi > out.txt") is None
    assert ExecTool._parse_plain_argv_chain("cat < notes.md") is None
    assert ExecTool._parse_plain_argv_chain("echo hi &") is None
    assert ExecTool._parse_plain_argv_chain('r""m -rf victim') is None
    assert ExecTool._parse_plain_argv_chain("echo 'unterminated") is None
    assert ExecTool._parse_plain_argv_chain("echo hi;; echo bad") is None


@pytest.mark.asyncio
async def test_plain_pipeline_runs_without_approval() -> None:
    tool = ExecTool()

    result = await tool.execute("echo hello | tr a-z A-Z")

    assert "HELLO" in result
    assert "requires explicit approval" not in result


@pytest.mark.asyncio
async def test_plain_chain_honors_and_or_and_sequence_semantics() -> None:
    tool = ExecTool()

    result = await tool.execute("false && echo skipped || echo fallback; echo done")

    assert "skipped" not in result
    assert "fallback" in result
    assert "done" in result


@pytest.mark.asyncio
async def test_plain_pipeline_preserves_quoted_arguments() -> None:
    tool = ExecTool()

    result = await tool.execute("printf '%s\\n' 'a b' | tr ' ' '_'")

    assert "a_b" in result


@pytest.mark.asyncio
async def test_destructive_pipeline_stays_blocked() -> None:
    tool = ExecTool()

    result = await tool.execute("echo ok; rm -rf /tmp/hermitcrab-not-deleted")

    assert "destructive command requires explicit approval" in result


def test_plain_argv_chain_marks_conditional_operators() -> None:
    chain = ExecTool._parse_plain_argv_chain("echo a | head -1 && echo b; echo c")

    assert chain is not None
    assert chain[0][0] is None
    assert chain[0][1] == [["echo", "a"], ["head", "-1"]]
    assert chain[1][0] == "&&"
    assert chain[1][1] == [["echo", "b"]]
    assert chain[2][0] == ";"
    assert chain[2][1] == [["echo", "c"]]


def test_find_actions_require_destructive_approval() -> None:
    assert ExecTool._classify_command_risk("find . -name '*.tmp' -delete") == "destructive"
    assert ExecTool._classify_command_risk("find . -type f -exec rm {} +") == "destructive"
    assert ExecTool._classify_command_risk("find . -name '*.py'") == "read_only"
