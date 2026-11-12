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
                "Execute a command with best-effort workspace path checks. Plain pipelines "
                "(|, ;, &&, ||) run as argv without a shell; other shell syntax (redirects, "
                "substitutions, expansions, globs) requires explicit approval. Destructive "
                "commands require explicit approval too. "
                "This is not a sandbox and should not be treated as confinement."
            )
        return (
            "Execute a command with full system access. Plain pipelines (|, ;, &&, ||) run "
            "as argv without a shell; other shell syntax and destructive commands require "
            "explicit approval. This is dangerous and not sandboxed."
        )

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
            return self._with_approval_hint(guard_error, command)
        uses_shell = self._requires_shell(command)
        argv_chain: list[tuple[str | None, list[list[str]]]] | None = None
        if uses_shell and not destructive_approved:
            argv_chain = self._parse_plain_argv_chain(command)
            if argv_chain is None:
                return self._with_approval_hint(
                    "Error: Command blocked by safety guard (shell syntax requires explicit approval)",
                    command,
                )
        if destructive_approved:
            self.clear_destructive_approval()

        try:
            if argv_chain is not None:
                return await self._run_argv_chain(argv_chain, cwd=cwd)
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

    _PLAIN_PIPELINE_OPERATORS = frozenset({"|", ";", "&&", "||"})
    _PLAIN_COMMAND_WORD_RE = re.compile(r"[A-Za-z0-9_./-]+")

    @classmethod
    def _parse_plain_argv_chain(
        cls, command: str
    ) -> list[tuple[str | None, list[list[str]]]] | None:
        """Parse plain argv segments joined by ``|``, ``;``, ``&&``, or ``||``.

        Returns ``None`` when the command needs a real shell: redirects, expansions, globs,
        background jobs, comments, or a quoted/escaped command word. Those commands keep
        the explicit-approval path, so the relaxed pipeline path never interprets shell
        language other than the supported operators.
        """
        parts: list[str] = []
        current: list[str] = []
        quote: str | None = None
        i = 0
        length = len(command)
        while i < length:
            ch = command[i]
            if quote == "'":
                current.append(ch)
                if ch == "'":
                    quote = None
                i += 1
                continue
            if quote == '"':
                if ch == "\\":
                    if i + 1 >= length:
                        return None
                    current.append(ch)
                    current.append(command[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    quote = None
                    current.append(ch)
                    i += 1
                    continue
                if ch in "$`":
                    return None
                current.append(ch)
                i += 1
                continue
            if ch == "\\":
                if i + 1 >= length or command[i + 1] == "\n":
                    return None
                current.append(ch)
                current.append(command[i + 1])
                i += 2
                continue
            if ch in ("'", '"'):
                quote = ch
                current.append(ch)
                i += 1
                continue
            if ch == "|":
                if i + 1 < length and command[i + 1] == "&":
                    return None  # |& is a shell feature.
                parts.append("".join(current))
                if i + 1 < length and command[i + 1] == "|":
                    parts.append("||")
                    i += 2
                else:
                    parts.append("|")
                    i += 1
                current = []
                continue
            if ch == "&":
                if i + 1 >= length or command[i + 1] != "&":
                    return None  # Background jobs need a shell.
                parts.append("".join(current))
                parts.append("&&")
                current = []
                i += 2
                continue
            if ch == ";":
                parts.append("".join(current))
                parts.append(";")
                current = []
                i += 1
                continue
            if ch in "<>$(){}*?[]~`\n#":
                return None
            current.append(ch)
            i += 1
        if quote is not None:
            return None
        parts.append("".join(current))

        if not parts or len(parts) % 2 != 1:
            return None
        chain: list[tuple[str | None, list[list[str]]]] = []
        operator: str | None = None
        group_operator: str | None = None
        pipeline: list[list[str]] = []
        for index, part in enumerate(parts):
            if index % 2 == 1:
                if part not in cls._PLAIN_PIPELINE_OPERATORS:
                    return None
                operator = part
                continue
            argv = cls._plain_segment_argv(part)
            if argv is None:
                return None
            if not pipeline:
                group_operator = operator
            pipeline.append(argv)
            next_operator = parts[index + 1] if index + 1 < len(parts) else None
            if next_operator is None or next_operator in {";", "&&", "||"}:
                chain.append((group_operator, pipeline))
                operator = None
                group_operator = None
                pipeline = []
        if not chain or pipeline:
            return None
        return chain

    @classmethod
    def _plain_segment_argv(cls, segment: str) -> list[str] | None:
        """Return argv for a plain pipeline segment, or None when it is not plain."""
        raw = segment.strip()
        if not raw:
            return None
        match = cls._PLAIN_COMMAND_WORD_RE.match(raw)
        if match is None:
            return None
        word_end = match.end()
        if word_end < len(raw) and not raw[word_end].isspace():
            return None  # Quoted, escaped, or expanded command word: shell territory.
        try:
            argv = shlex.split(raw, posix=True)
        except ValueError:
            return None
        if not argv:
            return None
        first = Path(argv[0]).name.lower()
        if cls._looks_destructive(first, raw.lower()):
            return None
        return argv

    async def _run_argv_chain(
        self,
        chain: list[tuple[str | None, list[list[str]]]],
        *,
        cwd: str,
    ) -> str:
        """Execute parsed argv segments without invoking a shell."""
        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        last_ok = True
        last_returncode: int | None = 0
        timed_out = False
        for operator, pipeline in chain:
            if operator == "&&" and not last_ok:
                last_ok = False
                continue
            if operator == "||" and last_ok:
                last_ok = True
                continue
            stdout, stderr, returncode, group_timed_out = await self._run_argv_pipeline(
                pipeline, cwd=cwd
            )
            stdout_parts.append(stdout)
            stderr_parts.append(stderr)
            last_returncode = returncode
            timed_out = timed_out or group_timed_out
            last_ok = returncode == 0 and not group_timed_out
            if group_timed_out:
                break
        return self._format_command_result(
            stdout=b"\n".join(part for part in stdout_parts if part),
            stderr=b"\n".join(part for part in stderr_parts if part),
            returncode=last_returncode,
            timed_out=timed_out,
        )

    async def _run_argv_pipeline(
        self,
        pipeline: list[list[str]],
        *,
        cwd: str,
    ) -> tuple[bytes, bytes, int | None, bool]:
        """Run one argv pipeline, connecting stages with OS pipes instead of a shell."""
        processes: list[asyncio.subprocess.Process] = []
        read_fd: int | None = None
        try:
            for index, argv in enumerate(pipeline):
                write_fd = None
                next_read_fd = None
                if index + 1 < len(pipeline):
                    next_read_fd, write_fd = os.pipe()
                try:
                    process = await asyncio.create_subprocess_exec(
                        *argv,
                        stdin=read_fd if read_fd is not None else asyncio.subprocess.DEVNULL,
                        stdout=write_fd if write_fd is not None else asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=cwd,
                    )
                except OSError as exc:
                    for fd in (read_fd, write_fd, next_read_fd):
                        if fd is not None:
                            try:
                                os.close(fd)
                            except OSError:
                                pass
                    read_fd = None
                    await self._terminate_processes(processes)
                    message = f"{argv[0]}: {exc.strerror or exc}".encode()
                    return b"", message, 127, False
                if read_fd is not None:
                    os.close(read_fd)
                    read_fd = None
                if write_fd is not None:
                    os.close(write_fd)
                processes.append(process)
                read_fd = next_read_fd
            assert processes and processes[-1].stdout is not None
            stderr_tasks = [
                asyncio.create_task(process.stderr.read())
                for process in processes
                if process.stderr is not None
            ]

            async def _collect() -> tuple[bytes, bytes, int | None]:
                stdout = await processes[-1].stdout.read()
                await asyncio.gather(*(process.wait() for process in processes))
                stderr_blocks = await asyncio.gather(*stderr_tasks)
                return (
                    stdout,
                    b"".join(block for block in stderr_blocks if block),
                    processes[-1].returncode,
                )

            try:
                stdout, stderr, returncode = await asyncio.wait_for(
                    _collect(), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                await self._terminate_processes(processes)
                for task in stderr_tasks:
                    task.cancel()
                await asyncio.gather(*stderr_tasks, return_exceptions=True)
                return b"", b"", None, True
            return stdout, stderr, returncode, False
        finally:
            if read_fd is not None:
                try:
                    os.close(read_fd)
                except OSError:
                    pass

    @staticmethod
    async def _terminate_processes(processes: list[asyncio.subprocess.Process]) -> None:
        """Terminate a spawned chain, escalating to kill after a short grace period."""
        for process in processes:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        if processes:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(process.wait() for process in processes)),
                    timeout=3.0,
                )
            except asyncio.TimeoutError:
                for process in processes:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                await asyncio.gather(
                    *(process.wait() for process in processes), return_exceptions=True
                )

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

    def _with_approval_hint(self, error: str, command: str) -> str:
        """Name the armed exact command when a different command was blocked."""
        approved = get_approved_destructive_command()
        if not approved or approved == self._normalize_command(command):
            return error
        return (
            f"{error}\nAn exact command was already approved for this turn: `{approved}`. "
            "Retry that command unchanged instead of a variant, or ask the user again."
        )

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

    @classmethod
    def _requires_shell(cls, command: str) -> bool:
        """Return True when the command uses shell syntax that argv execution cannot express.

        Quoted or backslash-escaped metacharacters are literal arguments, not shell syntax, so
        ``find . -name "*.py"`` runs through argv while ``ls | head`` and ``echo $HOME`` still
        require a shell. Such syntax is only run through ``create_subprocess_shell`` after the
        exact command is explicitly approved. Everything else runs via
        ``create_subprocess_exec`` with a parsed argv, so there is no shell to interpret
        metacharacters.
        """
        i = 0
        length = len(command)
        while i < length:
            ch = command[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                end = command.find("'", i + 1)
                if end == -1:
                    return True  # Unterminated quote: let the shell report the error.
                i = end + 1
                continue
            if ch == '"':
                next_index = cls._scan_double_quoted(command, i + 1)
                if next_index == -1:
                    return True
                i = next_index
                continue
            if ch in ";&|<>$`~*?[]{}()\n":
                return True
            i += 1
        return False

    @staticmethod
    def _scan_double_quoted(command: str, start: int) -> int:
        """Return the index after a closing quote, or -1 when shell expansion is needed."""
        i = start
        while i < len(command):
            ch = command[i]
            if ch == "\\":
                i += 1
                if i < len(command) and command[i] in '$`"\\\n':
                    i += 1
                continue
            if ch == '"':
                return i + 1
            if ch in "$`":
                return -1  # Command or parameter expansion inside double quotes needs a shell.
            i += 1
        return -1  # Unterminated quote.

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
        if first == "find" and cls._find_invokes_destructive_action(lowered):
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
    def _find_invokes_destructive_action(command: str) -> bool:
        """Return True when find can delete files or execute another command."""
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            tokens = command.split()
        return any(
            token in {"-delete", "-exec", "-execdir", "-ok", "-okdir"} for token in tokens
        )

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
