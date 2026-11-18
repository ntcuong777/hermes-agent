"""Stage B11, plugin absent (spec 08b 9.2): the fork core registers NO `cwd` slash command and does not crash.

The Discord `/cwd` with path autocomplete is the plugin's (chat-workspaces, `workspace-slash`). Without the plugin the
core's registration runs as upstream's: no native `cwd` spec, and no `cwd` command reaches the tree.
"""

from types import SimpleNamespace

from gateway.config import PlatformConfig
from plugins.platforms.discord.adapter import DiscordAdapter, _native_slash_commands


class _Tree:
    def __init__(self):
        self.names = []

    def command(self, name, description=""):
        def decorate(fn):
            self.names.append(name)
            return fn
        return decorate

    def add_command(self, cmd):
        self.names.append(getattr(cmd, "name", None))


def _adapter():
    return DiscordAdapter(PlatformConfig(enabled=True, token="fake-token", extra={}))


def test_there_is_no_native_cwd_spec():
    assert not [spec for spec in _native_slash_commands() if spec[0] == "cwd"]


def test_registration_without_the_plugin_adds_no_cwd_command_and_does_not_crash():
    adapter = _adapter()
    tree = _Tree()
    adapter._client = SimpleNamespace(tree=tree)
    adapter._register_slash_commands()
    assert "cwd" not in tree.names
    assert "thread" in tree.names  # the native registrations still happen
