"""execute_code's `hermes_tools` must offer a tool the session defers behind tool_search.

The agent used to hand execute_code only the tools SENT to the model. A tool named in
`tools.tool_search.defer` (web_search, web_extract) is reachable through `tool_call` but is
not sent, so the sandbox stub left it out and `from hermes_tools import web_extract` raised
ImportError. The sandbox names are now the sent tools plus what `tool_call` can reach.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent import tool_executor
from tools.code_execution_tool import generate_hermes_tools_module


def make_agent(sent: set[str]) -> SimpleNamespace:
    return SimpleNamespace(
        valid_tool_names=sent, enabled_toolsets=None, disabled_toolsets=None
    )


def test_a_deferred_tool_the_session_can_reach_is_offered(monkeypatch) -> None:
    monkeypatch.setattr(
        tool_executor,
        "_tool_search_scoped_names",
        lambda agent: frozenset({"web_extract", "web_search"}),
    )
    names = tool_executor.sandbox_tool_names(make_agent({"read_file", "terminal"}))
    assert {"read_file", "terminal", "web_extract", "web_search"} <= set(names)


def test_the_stub_defines_the_deferred_web_tools(monkeypatch) -> None:
    monkeypatch.setattr(
        tool_executor,
        "_tool_search_scoped_names",
        lambda agent: frozenset({"web_extract", "web_search"}),
    )
    stub = generate_hermes_tools_module(
        tool_executor.sandbox_tool_names(make_agent({"read_file"}))
    )
    assert "def web_extract" in stub
    assert "def web_search" in stub


def test_a_tool_outside_the_session_scope_is_not_offered(monkeypatch) -> None:
    monkeypatch.setattr(
        tool_executor, "_tool_search_scoped_names", lambda agent: frozenset()
    )
    names = tool_executor.sandbox_tool_names(make_agent({"read_file"}))
    assert "web_extract" not in names


def test_no_sent_tools_keeps_the_all_sandbox_tools_default(monkeypatch) -> None:
    monkeypatch.setattr(
        tool_executor,
        "_tool_search_scoped_names",
        lambda agent: frozenset({"web_extract"}),
    )
    assert tool_executor.sandbox_tool_names(make_agent(set())) is None
