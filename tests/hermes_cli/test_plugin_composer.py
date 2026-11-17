"""The composer text a TUI key chord hands to a plugin slash command (``composer_text`` keyword)."""
from hermes_cli.plugin_command_dispatch import invoke_plugin_command
from hermes_cli.plugin_composer import bind_composer_text


def test_a_handler_naming_composer_text_gets_it_while_a_chord_is_running():
    def handler(raw, composer_text=None):
        return composer_text
    with bind_composer_text("two\nlines"):
        assert invoke_plugin_command(handler, "toggle") == "two\nlines"


def test_without_a_chord_the_keyword_is_not_passed_at_all():
    seen = {}
    def handler(raw, **kwargs):
        seen.update(kwargs)
    invoke_plugin_command(handler, "toggle", session_key="k")
    assert seen == {"session_key": "k"}


def test_an_empty_composer_is_text_and_a_missing_one_is_not():
    def handler(raw, composer_text="unset"):
        return composer_text
    with bind_composer_text(""):
        assert invoke_plugin_command(handler, "") == ""
    with bind_composer_text(None):  # an image is attached: the client sent no text
        assert invoke_plugin_command(handler, "") == "unset"


def test_handlers_that_do_not_ask_for_it_are_unaffected_and_the_binding_ends_with_the_call():
    def legacy(raw):
        return raw
    with bind_composer_text("draft"):
        assert invoke_plugin_command(legacy, "x") == "x"
    def handler(raw, composer_text="unset"):
        return composer_text
    assert invoke_plugin_command(handler, "") == "unset"


def _registered(monkeypatch, **handlers):
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_command_handler", handlers.get)


def test_a_chord_offers_its_text_only_to_the_command_it_ran(monkeypatch):
    def toggle(raw, composer_text="unset"):
        return composer_text
    def other(raw, composer_text="unset"):
        return composer_text
    _registered(monkeypatch, toggle=toggle, other=other)
    with bind_composer_text("draft", "toggle"):
        assert invoke_plugin_command(toggle, "") == "draft"
        assert invoke_plugin_command(other, "") == "unset"  # a command a handler invokes from inside, or any other


def test_a_kwargs_handler_gets_no_composer_text_for_another_command(monkeypatch):
    seen = {}
    def toggle(raw, **kwargs):
        seen.update(kwargs)
    def other(raw, **kwargs):
        seen["other"] = kwargs
    _registered(monkeypatch, toggle=toggle, other=other)
    with bind_composer_text("draft", "toggle"):
        invoke_plugin_command(other, "", session_key="k")
        invoke_plugin_command(toggle, "")
    assert seen == {"other": {"session_key": "k"}, "composer_text": "draft"}


def test_a_command_with_no_bound_name_stays_open_to_any_handler():
    def handler(raw, composer_text="unset"):
        return composer_text
    with bind_composer_text("draft"):  # a caller that names no command keeps the old behaviour
        assert invoke_plugin_command(handler, "") == "draft"
