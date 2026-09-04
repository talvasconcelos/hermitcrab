"""Shell execution tool."""

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Any, Literal

from hermitcrab.agent.tools.base import Tool
from hermitcrab.agent.tools.context import (
    get_approved_destructive_command,
    set_approved_destructive_command,
)

CommandRisk = Literal["read_only", "workspace_write", "destructive"]


class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            r"\bdd\s+if=",  # dd
            r">\s*/dev/sd",  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",  # fork bomb
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        if self.restrict_to_workspace:
            return (
                "Execute a command directly (no shell) with best-effort workspace path checks. "
                "Shell syntax (pipes, redirects, variables) requires explicit approval. "
                "This is not a sandbox and should not be treated as confinement."
            )
        return "Execute a command with full system access; dangerous and not sandboxed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to execute"
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command"
                }
            },
            "required": ["command"]
        }

    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()
        destructive_approved = self._is_approved_destructive_command(command)
        guard_error = self._guard_command(
            command,
            cwd,
            destructive_approved=destructive_approved,
        )
        if guard_error:
            return guard_error
        uses_shell = self._requires_shell(command)
        if uses_shell and not destructive_approved:
            return "Error: Command blocked by safety guard (shell syntax requires explicit approval)"
        if destructive_approved:
            self.clear_destructive_approval()

        try:
            if uses_shell:
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
            else:
                try:
                    argv = shlex.split(command, posix=True)
                except ValueError:
                    argv = command.split()
                if not argv:
                    return "(no command)"
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3.0)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3.0)
                    except asyncio.TimeoutError:
                        stdout, stderr = b"", b""
                return self._format_command_result(
                    stdout=stdout,
                    stderr=stderr,
                    returncode=process.returncode,
                    timed_out=True,
                )

            return self._format_command_result(
                stdout=stdout,
                stderr=stderr,
                returncode=process.returncode,
                timed_out=False,
            )

        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _format_command_result(
        self,
        *,
        stdout: bytes | None,
        stderr: bytes | None,
        returncode: int | None,
        timed_out: bool,
    ) -> str:
        output_parts = []

        if timed_out:
            output_parts.append(f"Error: Command timed out after {self.timeout} seconds")

        if stdout:
            output_parts.append(stdout.decode("utf-8", errors="replace"))

        if stderr:
            stderr_text = stderr.decode("utf-8", errors="replace")
            if stderr_text.strip():
                output_parts.append(f"STDERR:\n{stderr_text}")

        if returncode not in (None, 0):
            output_parts.append(f"\nExit code: {returncode}")

        result = "\n".join(output_parts) if output_parts else "(no output)"

        max_len = 10000
        if len(result) > max_len:
            result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"

        return result

    def allow_destructive_command(self, command: str) -> None:
        """Allow one exact destructive command to run on the next execute call."""
        set_approved_destructive_command(self._normalize_command(command))

    def clear_destructive_approval(self) -> None:
        """Clear any one-shot destructive command approval."""
        set_approved_destructive_command(None)

    @classmethod
    def _normalize_command(cls, command: str) -> str:
        return " ".join(command.strip().split())

    def _is_approved_destructive_command(self, command: str) -> bool:
        approved = get_approved_destructive_command()
        if not approved:
            return False
        return approved == self._normalize_command(command)

    def _guard_command(
        self,
        command: str,
        cwd: str,
        *,
        destructive_approved: bool = False,
    ) -> str | None:
        """Best-effort safety guard with explicit risk classification."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        risk = self._classify_command_risk(cmd)
        workspace_root = Path(self.working_dir or cwd).resolve()
        if (
            risk == "destructive"
            and self.restrict_to_workspace
            and not self._requires_shell(cmd)
            and self._is_workspace_scoped_delete(cmd, workspace_root)
        ):
            risk = "workspace_write"
        if risk == "destructive" and not destructive_approved:
            return "Error: Command blocked by safety guard (destructive command requires explicit approval)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"
            if re.search(r"(?<![A-Za-z0-9_])(?:\$HOME|\$\{HOME\}|~[A-Za-z0-9_-]*)(?:/|$)", cmd):
                return "Error: Command blocked by safety guard (home path outside working dir)"
            cwd_path = Path(cwd).resolve()
            if cwd_path != workspace_root and workspace_root not in cwd_path.parents:
                return "Error: Command blocked by safety guard (working dir outside workspace)"

            for p in self._extract_command_paths(cmd):
                if p.is_absolute() and workspace_root not in p.parents and p != workspace_root:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @staticmethod
    def _extract_command_paths(command: str) -> list[Path]:
        """Extract obvious absolute paths from shell commands for workspace checks."""
        paths: list[Path] = []
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            tokens = command.split()

        for token in tokens:
            # Handle env assignments like FOO=/abs/path and args like --out=/abs/path.
            candidates = [token]
            if "=" in token:
                candidates.append(token.split("=", 1)[1])
            for candidate in candidates:
                if candidate.startswith("/") or re.match(r"^[A-Za-z]:\\", candidate):
                    paths.append(Path(candidate))

        # Catch paths adjacent to shell operators/redirections that shlex may leave embedded.
        paths.extend(Path(match) for match in re.findall(r"(?:^|[\s|>&;])(/[^\s\"'<>|;&]+)", command))
        paths.extend(Path(match) for match in re.findall(r"[A-Za-z]:\\[^\\\"'\s<>|;&]+", command))
        return paths

    @classmethod
    def _is_workspace_scoped_delete(cls, command: str, workspace_root: Path) -> bool:
        """Allow file deletion as an ordinary workspace write when it stays inside the workspace."""
        if cls._requires_shell(command):
            return False
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return False

        if not tokens or Path(tokens[0]).name.lower() not in {"rm", "rmdir", "del", "erase"}:
            return False
        if any(token in {"&&", "||", ";", "|"} for token in tokens):
            return False

        targets = [token for token in tokens[1:] if not token.startswith("-")]
        if not targets:
            return False

        workspace_root = workspace_root.resolve()
        for target in targets:
            target_path = Path(target).expanduser()
            if str(target).startswith(("~", "$HOME", "${HOME}")):
                return False
            if any(ch in target for ch in "*?[]"):
                return False
            resolved = target_path.resolve() if target_path.is_absolute() else (workspace_root / target_path).resolve()
            if resolved == workspace_root or workspace_root not in resolved.parents:
                return False
        return True

    _DESTRUCTIVE_COMMANDS = {
        "rm",
        "rmdir",
        "del",
        "erase",
        "format",
        "mkfs",
        "diskpart",
        "shutdown",
        "reboot",
        "poweroff",
        "chmod",
        "chown",
        "dd",
        "sudo",
    }

    _INTERPRETER_COMMANDS = {
        "sh",
        "bash",
        "zsh",
        "dash",
        "ksh",
        "fish",
        "csh",
        "tcsh",
        "node",
        "nodejs",
        "perl",
        "ruby",
        "php",
        "python",
        "python2",
        "python3",
        "pypy",
        "pypy3",
        "lua",
        "tclsh",
        "expect",
        "powershell",
        "pwsh",
        "env",
        "eval",
        "exec",
        "xargs",
    }

    _SHELL_METACHARACTER_RE = re.compile(r"[;&|><$`~*?\[\]{}()!\n]")

    @classmethod
    def _requires_shell(cls, command: str) -> bool:
        """Return True when the command uses shell syntax that argv execution cannot express.

        Such syntax is only run through ``create_subprocess_shell`` after the exact command is
        explicitly approved. Everything else runs via ``create_subprocess_exec`` with a parsed
        argv, so there is no shell to interpret metacharacters.
        """
        return bool(cls._SHELL_METACHARACTER_RE.search(command))

    @classmethod
    def _classify_command_risk(cls, command: str) -> CommandRisk:
        """Classify a command by its likely mutation risk.

        Shell syntax (pipes, redirects, operators) is a *separate* approval gate handled
        in ``execute``; it does not by itself make a command destructive. A read-only
        chain such as ``which nak; nak --version 2>/dev/null`` stays read-only, while a
        chain that hides a destructive payload (``echo ok|sh``, ``git status;rm victim``)
        remains destructive.
        """
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            tokens = command.split()

        if not tokens:
            return "read_only"

        lowered = command.lower()
        first = Path(tokens[0]).name.lower()

        if cls._requires_shell(command) and cls._shell_syntax_looks_destructive(command):
            return "destructive"
        if cls._looks_destructive(first, lowered):
            return "destructive"
        if cls._looks_read_only(first, lowered):
            return "read_only"
        return "workspace_write"

    @classmethod
    def _shell_syntax_looks_destructive(cls, command: str) -> bool:
        """Detect destructive content hidden behind shell operators/redirections.

        Enforcement does not depend on this helper: any shell-syntax command still
        requires explicit approval before running. This only upgrades a shell command to
        ``destructive`` when it actually mutates state, so a read-only chain is reported
        accurately instead of as a destructive action.
        """
        for segment in cls._shell_command_segments(command):
            try:
                tokens = shlex.split(segment, posix=True)
            except ValueError:
                tokens = segment.split()
            if not tokens:
                continue
            first = Path(tokens[0]).name.lower()
            if cls._looks_destructive(first, segment.lower()):
                return True
        return cls._shell_output_redirect_writes_file(command)

    @classmethod
    def _shell_command_segments(cls, command: str) -> list[str]:
        """Split a shell command on operators while respecting quotes and escapes."""
        segments: list[str] = []
        current: list[str] = []
        i = 0
        quote: str | None = None
        while i < len(command):
            ch = command[i]
            if quote is not None:
                current.append(ch)
                if ch == quote:
                    quote = None
                i += 1
                continue
            if ch in ("'", '"'):
                quote = ch
                current.append(ch)
                i += 1
                continue
            if ch == "\\":
                current.append(ch)
                if i + 1 < len(command):
                    current.append(command[i + 1])
                    i += 2
                else:
                    i += 1
                continue
            if command.startswith("&&", i) or command.startswith("||", i):
                segments.append("".join(current))
                current = []
                i += 2
                continue
            if ch in ";|&\n":
                segments.append("".join(current))
                current = []
                i += 1
                continue
            current.append(ch)
            i += 1
        segments.append("".join(current))
        return [segment.strip() for segment in segments if segment.strip()]

    @classmethod
    def _shell_output_redirect_writes_file(cls, command: str) -> bool:
        """Return True when a redirect writes to a real file rather than a sink/fd."""
        for match in re.finditer(r"\d*>>?\s*([^\s;|&<]+)", command):
            target = match.group(1)
            if target.startswith(("/dev/null", "/dev/stderr", "/dev/stdout", "&")):
                continue
            return True
        return False

    @classmethod
    def _looks_destructive(cls, first: str, lowered: str) -> bool:
        if first in cls._DESTRUCTIVE_COMMANDS:
            return True
        if first in cls._INTERPRETER_COMMANDS:
            return True
        if first.startswith(("python", "pypy", "mkfs.")):
            return True
        if first == "git":
            destructive_git_patterns = (
                "git reset",
                "git checkout --",
                "git clean",
                "git restore",
                "git revert --no-edit",
            )
            if any(pattern in lowered for pattern in destructive_git_patterns):
                return True
        return False

    @staticmethod
    def _looks_read_only(first: str, lowered: str) -> bool:
        return first in {
            "cat",
            "head",
            "tail",
            "less",
            "more",
            "wc",
            "ls",
            "find",
            "grep",
            "rg",
            "awk",
            "sed",
            "echo",
            "printf",
            "which",
            "where",
            "whoami",
            "pwd",
            "env",
            "printenv",
            "date",
            "cal",
            "df",
            "du",
            "free",
            "uptime",
            "uname",
            "file",
            "stat",
            "diff",
            "sort",
            "uniq",
            "tr",
            "cut",
            "paste",
            "test",
            "true",
            "false",
            "type",
            "readlink",
            "realpath",
            "basename",
            "dirname",
            "sha256sum",
            "md5sum",
            "b3sum",
            "xxd",
            "hexdump",
            "od",
            "strings",
            "tree",
            "jq",
            "yq",
            "git",
            "gh",
        } and "-i " not in lowered and "--in-place" not in lowered
