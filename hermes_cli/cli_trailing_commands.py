"""Bare ``/command`` lines peeled off the tail of a skill instruction.

The CLI methods are one-line calls into here. A line peels only when it is a
bare token this CLI can dispatch without I/O.
"""

from __future__ import annotations


def is_dispatchable_command_line(cli, line: str) -> bool:
    import cli as cli_mod
    if not cli_mod._looks_like_slash_command(line) or any(ch.isspace() for ch in line):
        return False
    token = line[1:].lower()
    if cli._slash_handler(token):
        return True
    if f"/{token}" in cli_mod._session_skill_commands(cli) or f"/{token}" in cli_mod.get_skill_bundles():
        return True
    return token in cli.config.get("quick_commands", {}) or token in cli_mod._get_plugin_cmd_handler_names()


def split_trailing_command_lines(cli, instruction: str) -> tuple[str, list[str]]:
    lines = (instruction or "").splitlines()
    cut = len(lines)
    while cut and is_dispatchable_command_line(cli, lines[cut - 1].strip()):
        cut -= 1
    return "\n".join(lines[:cut]).strip(), [line.strip() for line in lines[cut:]]


def dispatch_trailing_commands(cli, commands: list[str]) -> None:
    for command in commands:
        if not cli.process_command(command):
            cli._should_exit = True
            app = getattr(cli, "_app", None)
            if app is not None and app.is_running:
                app.exit()
            break
