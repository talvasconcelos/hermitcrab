"""Warnings must reach a file even when the console is silenced.

Regression: the CLI silenced the console with ``logger.disable("hermitcrab")``, which
discards records *before* any handler, and no sink ever wrote to disk. A failed run with
console logging off therefore left nothing to inspect — only the sanitised message.

The probe logs from a module named ``hermitcrab.*`` on purpose: a test-module logger would
not be muted by the namespace disable and would pass while production stayed silent.
"""

import logging
import sys
import types

from loguru import logger

from hermitcrab.cli.commands import _configure_runtime_logging, _InterceptStdlibLogging
from hermitcrab.config.schema import Config


def _emit_warning_as_hermitcrab_module(marker: str) -> None:
    module = types.ModuleType("hermitcrab.logging_probe")
    exec(  # noqa: S102 - deliberate: the logger name comes from the module's __name__
        f"from loguru import logger\ndef emit():\n    logger.warning({marker!r})\n",
        module.__dict__,
    )
    sys.modules[module.__name__] = module
    module.emit()


def test_warning_lands_in_file_and_stays_off_the_console(tmp_path, capsys) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    try:
        _configure_runtime_logging(config, logs=False)
        _emit_warning_as_hermitcrab_module("smoke-marker-12345")
    finally:
        logger.remove()

    log_file = config.system_root_path / "logs" / "hermitcrab.log"
    assert "smoke-marker-12345" in log_file.read_text(encoding="utf-8")
    assert "smoke-marker-12345" not in capsys.readouterr().err


def test_logs_flag_puts_warnings_on_the_console(tmp_path, capsys) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    try:
        _configure_runtime_logging(config, logs=True)
        _emit_warning_as_hermitcrab_module("console-marker-67890")
    finally:
        logger.remove()

    assert "console-marker-67890" in capsys.readouterr().err


def test_stdlib_warnings_reach_the_file_too(tmp_path) -> None:
    """LiteLLM's provider warnings come through stdlib logging, not loguru."""
    config = Config.model_validate({"root": str(tmp_path)})
    try:
        _configure_runtime_logging(config, logs=False)
        # pytest swaps the root handlers for its capture, so drive the bridge directly.
        _InterceptStdlibLogging().emit(
            logging.LogRecord(
                "LiteLLM", logging.WARNING, __file__, 1, "stdlib-marker-24680", None, None
            )
        )
    finally:
        logger.remove()

    log_file = config.system_root_path / "logs" / "hermitcrab.log"
    assert "stdlib-marker-24680" in log_file.read_text(encoding="utf-8")
