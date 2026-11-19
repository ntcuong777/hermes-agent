"""The class A fork patches live outside core now; none of them may come back into it.

Where they went: the `core-fixes` plugin (nix-darwin hermes/plugins/core-fixes: string terminal
timeouts, the mid-prompt /skill note) and the `pricing:` rows of each host config.yaml (Claude
Sonnet 5.5, Z.AI GLM-5.3). Re-adding one here would apply it twice. If a test fails after an
upstream merge, upstream itself now carries the fix: drop the plugin seam or the config row
instead of keeping both.
"""

from __future__ import annotations

import inspect


def test_the_inline_skill_note_is_not_in_core():
    import agent.skill_commands as sc

    assert not hasattr(sc, "inline_skill_note")
    assert not hasattr(sc, "_is_user_only_skill")


def test_the_tui_turn_path_adds_no_inline_skill_note():
    from tui_gateway import prompt_turn

    assert "inline_skill_note" not in inspect.getsource(prompt_turn)


def test_the_terminal_plan_has_no_string_timeout_coercion():
    from tools import terminal_tool

    assert "Models sometimes send the number as a string" not in inspect.getsource(terminal_tool._plan_execution)


def test_the_bundled_price_table_has_no_fork_rows():
    from agent.usage_pricing import _OFFICIAL_DOCS_PRICING

    assert ("anthropic", "claude-sonnet-5-5") not in _OFFICIAL_DOCS_PRICING
    assert ("zai", "glm-5.3-flash") not in _OFFICIAL_DOCS_PRICING
    assert ("zai", "glm-5.3") not in _OFFICIAL_DOCS_PRICING
