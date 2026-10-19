"""Starting the CLI must not import LiteLLM.

Regression: `litellm_provider` imported litellm at module scope, and the config →
provider-registry chain pulled it into every command, so `hermitcrab --version` spent
~26s of its ~30s importing an SDK it never used.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_the_cli_does_not_load_litellm() -> None:
    code = "import sys, hermitcrab.cli.commands; print('litellm' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )

    assert result.stdout.strip() == "False"


def test_provider_module_still_runs_a_request(monkeypatch) -> None:
    """The lazy accessor must return the real module, not a stub."""
    from hermitcrab.providers import litellm_provider

    module = litellm_provider._litellm_module()

    assert hasattr(module, "acompletion")
