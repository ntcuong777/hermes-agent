"""A bare ``/command`` on its own line after a skill command runs as a command.

Regression for the C-g editor round-trip report: the composer held ``/caveman`` on the first line
and ``/exit`` on the second, and Enter loaded the skill with ``/exit`` as its instruction — the
text went to the model instead of quitting. Whole-line bare commands at the tail of a skill (or
bundle) instruction dispatch in order; anything else (mid-sentence ``/help``, ``/usr/local``)
stays instruction text.
"""

from unittest.mock import patch

import cli as cli_mod
from tests.hermes_cli.test_cli_init import _make_cli


def _skill_cli(monkeypatch, queued):
    """CLI shell whose skill table knows only /caveman; the built message lands in *queued*."""

    def _fake_build(cmd_key, user_instruction="", task_id=None, runtime_note=""):
        queued.append((cmd_key, user_instruction))
        return "SKILL MESSAGE"

    import agent.skill_commands as sc

    monkeypatch.setattr(sc, "build_skill_invocation_message", _fake_build)
    monkeypatch.setattr("hermes_cli.gateway_plugin_commands.lookup", lambda cli, bare: None)
    monkeypatch.setattr(
        "hermes_cli.gateway_plugin_commands.skills",
        lambda cli: {"/caveman": {"name": "caveman", "description": "Terse mode"}})
    cli = _make_cli()
    monkeypatch.setattr(cli, "_queue_loaded_skills", lambda msg, label, missing: None)
    return cli


def test_trailing_exit_line_dispatches_not_instructs(monkeypatch):
    """`/caveman\\n/exit` loads the skill with no instruction and runs /exit."""
    queued = []
    cli = _skill_cli(monkeypatch, queued)
    exits = []
    monkeypatch.setattr(cli, "_cmd_exit", lambda command: exits.append(command) or False)

    cli.process_command("/caveman\n/exit")

    assert queued == [("/caveman", "")], "the skill must load without /exit as its instruction"
    assert exits == ["/exit"], "the trailing /exit line must dispatch as a command"
    assert cli._should_exit is True, "a False-returning trailing command ends the REPL"


def test_instruction_text_is_kept(monkeypatch):
    """`/caveman\\nfix the bug` keeps its instruction."""
    queued = []
    cli = _skill_cli(monkeypatch, queued)

    cli.process_command("/caveman\nfix the bug")

    assert queued == [("/caveman", "fix the bug")]


def test_mid_sentence_slash_and_paths_stay_instruction(monkeypatch):
    """`see /help there` and `/usr/local` lines are prose, not commands."""
    queued = []
    cli = _skill_cli(monkeypatch, queued)
    exits = []
    monkeypatch.setattr(cli, "_cmd_exit", lambda command: exits.append(command) or False)

    cli.process_command("/caveman\nsee /help there\n/usr/local/bin\n/exit")

    assert queued == [("/caveman", "see /help there\n/usr/local/bin")]
    assert exits == ["/exit"]


def test_unknown_bare_line_stays_instruction(monkeypatch):
    """A bare line that resolves to nothing keeps riding along as instruction text."""
    queued = []
    cli = _skill_cli(monkeypatch, queued)
    exits = []
    monkeypatch.setattr(cli, "_cmd_exit", lambda command: exits.append(command) or False)

    cli.process_command("/caveman\n/no-such-command-anywhere\n/exit")

    assert queued == [("/caveman", "/no-such-command-anywhere")]
    assert exits == ["/exit"]


def test_split_helper_peels_only_bare_resolvable_lines():
    """Unit shape of the peel: whole lines only, resolved tokens only, order kept."""
    cli = _make_cli()
    instruction, trailing = cli._split_trailing_command_lines("explain this\n/help\n/exit")
    assert instruction == "explain this"
    assert trailing == ["/help", "/exit"]
    instruction, trailing = cli._split_trailing_command_lines("no commands /help here")
    assert trailing == []
    assert patch  # imported for parity with the dispatch tests
