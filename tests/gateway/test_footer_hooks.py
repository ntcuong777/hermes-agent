"""The ``footer`` and ``usage_extra`` plugin hooks reach the runtime footer and /usage; a raising hook costs nothing."""

from unittest.mock import patch

from gateway import footer_hooks, runtime_footer


def _hooks(**by_name):
    return lambda name, **kw: by_name[name](**kw) if callable(by_name.get(name)) else by_name.get(name, [])


def test_footer_contributions_follow_the_built_in_line():
    with patch("hermes_cli.plugins.invoke_hook", _hooks(footer=["📊 quota\n"])):
        assert footer_hooks.with_footer_extras("model · 12%") == "model · 12%\n📊 quota"


def test_no_contribution_leaves_the_line_alone():
    with patch("hermes_cli.plugins.invoke_hook", _hooks(footer=[None, "  "])):
        assert footer_hooks.with_footer_extras("model") == "model"


def test_extras_stand_alone_when_the_built_in_line_is_empty():
    with patch("hermes_cli.plugins.invoke_hook", _hooks(footer=["📊 quota"])):
        assert footer_hooks.with_footer_extras("") == "📊 quota"


def test_a_raising_hook_is_skipped_not_raised():
    def boom(**_):
        raise RuntimeError("x")
    with patch("hermes_cli.plugins.invoke_hook", _hooks(footer=boom, usage_extra=boom)):
        assert footer_hooks.with_footer_extras("m") == "m"
        assert footer_hooks.usage_extra_lines() == []


def test_usage_extra_splits_into_lines():
    with patch("hermes_cli.plugins.invoke_hook", _hooks(usage_extra=["a\nb", None])):
        assert footer_hooks.usage_extra_lines() == ["a", "b"]


def test_build_footer_line_calls_the_seam():
    cfg = {"display": {"runtime_footer": {"enabled": True, "fields": ["model"]}}}
    with patch("hermes_cli.plugins.invoke_hook", _hooks(footer=["📊 q"])):
        line = runtime_footer.build_footer_line(user_config=cfg, platform_key=None, model="m", context_tokens=0, context_length=None)
    assert line.endswith("\n📊 q")


def test_the_hook_names_are_registered():
    from hermes_cli.plugins import VALID_HOOKS
    assert {"footer", "usage_extra"} <= VALID_HOOKS
