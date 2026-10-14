"""A model that calls a deferred tool by its own name is told how to reach it.

Tool search sends a few tools in full and keeps the rest behind ``tool_search`` /
``tool_describe`` / ``tool_call``. A model that calls ``web_extract`` directly got
``Tool 'web_extract' does not exist. Available tools: ...``, a list without it, and
concluded the tool was missing (session 20261014_095409_82760b). The tool exists; it is
just not sent. The error must say so and name the way in.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.conversation_loop import _invalid_tool_name_error_content
from agent.turn_tool_validation import deferred_tool_names_for_errors

SENT = {"terminal", "read_file", "tool_search", "tool_describe", "tool_call"}


def test_a_deferred_tool_name_points_at_tool_call():
    content = _invalid_tool_name_error_content(
        "web_extract", SENT, reachable_deferred=frozenset({"web_extract"})
    )
    assert "web_extract" in content and "tool_call" in content
    assert "does not exist" not in content


def test_a_name_that_exists_nowhere_keeps_the_old_error():
    content = _invalid_tool_name_error_content(
        "web_extrakt", SENT, reachable_deferred=frozenset({"web_extract"})
    )
    assert content.startswith("Tool 'web_extrakt' does not exist. Available tools:")


def test_the_old_two_argument_call_still_works():
    assert "does not exist" in _invalid_tool_name_error_content("nope", SENT)


def test_a_blank_name_stays_terse():
    content = _invalid_tool_name_error_content(
        " ", SENT, reachable_deferred=frozenset({"web_extract"})
    )
    assert "web_extract" not in content


def test_the_reachable_set_is_empty_unless_tool_call_is_sent(monkeypatch):
    import agent.tool_executor as te

    monkeypatch.setattr(
        te, "_tool_search_scoped_names", lambda agent: frozenset({"web_extract"})
    )
    with_call = SimpleNamespace(valid_tool_names=SENT)
    without_call = SimpleNamespace(valid_tool_names={"terminal"})
    assert deferred_tool_names_for_errors(with_call) == frozenset({"web_extract"})
    assert deferred_tool_names_for_errors(without_call) == frozenset()
