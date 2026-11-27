"""A plugin can add completion words to a BUILT-IN command (`register_command_words`, fork).

`/memory` is a built-in with a fixed word table; the words a plugin handles for it (`/memory relearn`) were never offered, and
`register_command` refuses a name a built-in owns. The built-in's own words stay first and unchanged; the plugin's are added
after them, a second word completes from its choices, and nothing here changes what a command does.
"""

from prompt_toolkit.document import Document

from hermes_cli import plugins as plugins_mod
from hermes_cli.commands import SUBCOMMANDS
from hermes_cli.commands_completion import SlashCommandCompleter
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

WORDS = {"show": (), "review": (), "relearn": (), "approve": ("all",), "reject": ("all",)}


def complete(monkeypatch, text, words=None):
    monkeypatch.setattr(plugins_mod, "get_command_words", lambda: {"memory": dict(WORDS if words is None else words)})
    monkeypatch.setattr(plugins_mod, "get_plugin_commands", lambda: {})
    completer = SlashCommandCompleter()
    return [c.text for c in completer.get_completions(Document(text, len(text)), None)]


def test_the_builtins_own_words_come_first_then_the_plugins(monkeypatch):
    own = list(SUBCOMMANDS["/memory"])
    got = complete(monkeypatch, "/memory ")
    assert got[: len(own)] == own
    assert {"show", "review", "relearn"} <= set(got[len(own):])


def test_a_word_the_builtin_already_has_is_not_offered_twice(monkeypatch):
    got = complete(monkeypatch, "/memory ")
    assert got.count("approve") == 1 and got.count("reject") == 1


def test_a_partial_word_reaches_a_plugin_word(monkeypatch):
    assert complete(monkeypatch, "/memory re") == [w for w in [*SUBCOMMANDS["/memory"], "show", "review", "relearn"] if w.startswith("re")]
    assert "relearn" in complete(monkeypatch, "/memory rel")


def test_the_second_word_completes_from_the_plugins_choices(monkeypatch):
    assert complete(monkeypatch, "/memory approve ") == ["all"]
    assert complete(monkeypatch, "/memory reject a") == ["all"]


def test_a_word_without_choices_and_a_third_word_offer_nothing(monkeypatch):
    assert complete(monkeypatch, "/memory relearn ") == []
    assert complete(monkeypatch, "/memory approve all ") == []
    assert complete(monkeypatch, "/memory nosuch ") == []


def test_without_registered_words_a_builtin_is_exactly_as_before(monkeypatch):
    assert complete(monkeypatch, "/memory ", words={}) == list(SUBCOMMANDS["/memory"])


def test_words_for_another_command_do_not_leak(monkeypatch):
    monkeypatch.setattr(plugins_mod, "get_command_words", lambda: {"skills": {"relearn": ()}})
    monkeypatch.setattr(plugins_mod, "get_plugin_commands", lambda: {})
    got = [c.text for c in SlashCommandCompleter().get_completions(Document("/memory ", 8), None)]
    assert "relearn" not in got


def test_a_broken_registry_never_breaks_typing(monkeypatch):
    def boom():
        raise RuntimeError("plugins down")

    monkeypatch.setattr(plugins_mod, "get_command_words", boom)
    monkeypatch.setattr(plugins_mod, "get_plugin_commands", lambda: {})
    got = [c.text for c in SlashCommandCompleter().get_completions(Document("/memory ", 8), None)]
    assert got == list(SUBCOMMANDS["/memory"])


def _ctx(name="demo"):
    manager = PluginManager()
    manifest = PluginManifest(name=name, source="user")
    return PluginContext(manifest, manager), manager


def _merged(monkeypatch, *managers):
    """get_command_words over one manager (what discovery would have filled)."""
    monkeypatch.setattr(plugins_mod, "_ensure_plugins_discovered", lambda: managers[0])
    return plugins_mod.get_command_words()


def test_register_command_words_stores_a_normalised_table(monkeypatch):
    ctx, manager = _ctx()
    assert ctx.register_command_words("/Memory", {"Relearn": (), "approve": ["all"]}) is not None
    assert _merged(monkeypatch, manager) == {"memory": {"relearn": (), "approve": ("all",)}}


def test_register_command_words_accepts_a_plain_list_and_ignores_nothing_to_add(monkeypatch):
    ctx, manager = _ctx()
    assert ctx.register_command_words("memory", ["show", "review"]) is not None
    assert ctx.register_command_words("skills", []) is None
    assert ctx.register_command_words("", ["x"]) is None
    assert _merged(monkeypatch, manager) == {"memory": {"show": (), "review": ()}}


def test_two_plugins_extending_one_builtin_add_to_each_other(monkeypatch):
    """write-approval-commands owns show/approve-all, memory-shape owns relearn: neither may replace the other."""
    manager = PluginManager()
    PluginContext(PluginManifest(name="a", source="user"), manager).register_command_words(
        "memory", {"show": (), "approve": ("all",)})
    PluginContext(PluginManifest(name="b", source="user"), manager).register_command_words(
        "memory", {"relearn": (), "approve": ("once",)})
    got = _merged(monkeypatch, manager)["memory"]
    assert set(got) == {"show", "approve", "relearn"}
    assert got["approve"] == ("all", "once")
