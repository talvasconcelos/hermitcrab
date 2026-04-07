"""Shell execution tool."""

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Any, Literal

from hermitcrab.agent.tools.base import Tool

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
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
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
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error

        try:
            process = await asyncio.create_subprocess_shell(
                command,
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

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard with explicit risk classification."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        risk = self._classify_command_risk(cmd)
        if risk == "destructive":
            return "Error: Command blocked by safety guard (destructive command requires explicit approval)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            # Only match absolute paths — avoid false positives on relative
            # paths like ".venv/bin/python" where "/bin/python" would be
            # incorrectly extracted by the old pattern.
            posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", cmd)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw.strip()).resolve()
                except Exception:
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @classmethod
    def _classify_command_risk(cls, command: str) -> CommandRisk:
        """Classify a shell command by its likely mutation risk."""
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            tokens = command.split()

        if not tokens:
            return "read_only"

        first = Path(tokens[0]).name.lower()
        lowered = command.lower()

        if cls._looks_destructive(first, tokens, lowered):
            return "destructive"
        if cls._looks_read_only(first, lowered):
            return "read_only"
        return "workspace_write"

    @staticmethod
    def _looks_destructive(first: str, tokens: list[str], lowered: str) -> bool:
        if first in {
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
        }:
            return True

        if first == "mv":
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

        if any(operator in lowered for operator in (" > ", " >> ")):
            return True

        if len(tokens) >= 2 and first in {"python", "python3"} and tokens[1] == "-c":
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
