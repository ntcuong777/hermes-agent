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
