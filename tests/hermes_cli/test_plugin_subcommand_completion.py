"""A plugin command can announce its subcommands, and the slash completer offers them (P19).

`/tell ` then lists the words, `/tell approve ` lists the choices. Both the classic CLI and the Ink TUI
(`complete.slash` builds the same `SlashCommandCompleter`) get it. Built-in commands are unaffected.
"""

from prompt_toolkit.document import Document

from hermes_cli import plugins as plugins_mod
from hermes_cli.commands_completion import SlashCommandCompleter
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

TELL = {
    "handler": lambda raw: "ok",
    "description": "Reach a session",
    "args_hint": "[list|approve]",
    "plugin": "session-inbox",
    "subcommands": {"list": (), "approve": ("once", "session"), "watch": ("on", "off")},
}


def complete(monkeypatch, text, commands=None):
    monkeypatch.setattr(
        plugins_mod, "get_plugin_commands", lambda: dict(commands or {"tell": TELL})
    )
    completer = SlashCommandCompleter()
    return [c.text for c in completer.get_completions(Document(text, len(text)), None)]


def test_the_words_are_offered_after_the_command_and_a_space(monkeypatch):
    assert set(complete(monkeypatch, "/tell ")) == {"list", "approve", "watch"}


def test_a_partial_word_is_narrowed(monkeypatch):
    assert complete(monkeypatch, "/tell ap") == ["approve"]


def test_the_choices_of_a_word_are_offered_after_it(monkeypatch):
    assert set(complete(monkeypatch, "/tell approve ")) == {"once", "session"}
    assert complete(monkeypatch, "/tell approve o") == ["once"]
    assert complete(monkeypatch, "/tell watch ") == ["on", "off"]


def test_a_word_without_choices_and_a_third_word_offer_nothing(monkeypatch):
    assert complete(monkeypatch, "/tell list ") == []
    assert complete(monkeypatch, "/tell approve once ") == []
    assert complete(monkeypatch, "/tell nosuch ") == []


def test_a_plain_list_of_words_works_too(monkeypatch):
    plain = dict(TELL, subcommands=("alpha", "beta"))
    assert complete(monkeypatch, "/tell ", {"tell": plain}) == ["alpha", "beta"]


def test_a_command_without_subcommands_offers_nothing(monkeypatch):
    bare = {k: v for k, v in TELL.items() if k != "subcommands"}
    assert complete(monkeypatch, "/tell ", {"tell": bare}) == []


def test_a_broken_plugin_listing_never_breaks_typing(monkeypatch):
    def boom():
        raise RuntimeError("plugins down")

    monkeypatch.setattr(plugins_mod, "get_plugin_commands", boom)
    completer = SlashCommandCompleter()
    assert list(completer.get_completions(Document("/tell ", 6), None)) == []


def test_a_builtin_keeps_its_own_subcommands(monkeypatch):
    got = complete(monkeypatch, "/reasoning ")
    assert "high" in got and "list" not in got


def test_register_command_stores_the_subcommands_normalised():
    mgr = PluginManager()
    ctx = PluginContext(PluginManifest(name="p", source="user"), mgr)
    ctx.register_command(
        "tell",
        lambda a: a,
        description="t",
        subcommands={"Approve": ["once", "session"], "list": None},
    )
    ctx.register_command("plain", lambda a: a, description="t", subcommands=["a", "b"])
    ctx.register_command("none", lambda a: a, description="t")
    assert mgr._plugin_commands["tell"]["subcommands"] == {
        "approve": ("once", "session"),
        "list": (),
    }
    assert mgr._plugin_commands["plain"]["subcommands"] == {"a": (), "b": ()}
    assert mgr._plugin_commands["none"]["subcommands"] == {}
