"""Tests for the ``transform_request_messages`` plugin hook apply site.

Egress backstop (task 06 / spec step 2.2): the hook rewrites ONLY the outgoing
provider request copy, at the same seam as ``ContextEngine.select_context``
results. Contract mirrored from ``_apply_context_engine_selection``: fail-open
on exceptions and invalid returns, persisted history never mutated (the hook
receives structural clones), first valid list of dicts wins, and the no-plugin
path is byte-identical (gated on ``has_hook`` before any copy is made).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from agent.conversation_loop import _apply_transform_request_messages_plugins


REQUEST = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hello"},
]


def _agent() -> Any:
    agent = MagicMock()
    agent.session_id = "test-session"
    agent.model = "test-model"
    agent.platform = "cli"
    return agent


def test_no_plugin_registered_returns_request_untouched_without_invoking():
    """has_hook=False short-circuits: same list object, no copies, no dispatch."""
    agent = _agent()
    logger = MagicMock()
    with patch("hermes_cli.lifecycle.has_hook", return_value=False) as _hh, \
            patch("hermes_cli.lifecycle.invoke_hook") as _ih:
        out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=logger)
    assert out is REQUEST
    _hh.assert_called_once_with("transform_request_messages")
    _ih.assert_not_called()
    assert not logger.warning.called


def test_first_valid_list_wins_and_is_applied():
    agent = _agent()
    replacement = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "REDACTED"},
    ]
    seen = {}

    def _hook(hook_name, **kwargs):
        seen.update(kwargs)
        # A prior plugin declined... then the first valid replacement wins.
        return [None, replacement]

    with patch("hermes_cli.lifecycle.has_hook", return_value=True), \
            patch("hermes_cli.lifecycle.invoke_hook", side_effect=_hook):
        out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=MagicMock())
    assert out is replacement
    assert [m["content"] for m in seen["messages"]] == ["sys", "hello"]
    assert seen["session_id"] == "test-session"
    assert seen["model"] == "test-model"


def test_hook_receives_clones_persisted_history_safe():
    """A hook mutating the list it received must not reach the caller's request."""
    agent = _agent()

    def _hook(hook_name, **kwargs):
        kwargs["messages"][0]["content"] = "TAMPERED"
        kwargs["messages"].append({"role": "user", "content": "INJECTED"})
        yield None

    with patch("hermes_cli.lifecycle.has_hook", return_value=True), \
            patch("hermes_cli.lifecycle.invoke_hook", side_effect=_hook):
        out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=MagicMock())
    assert out is REQUEST
    assert REQUEST == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]


def test_invalid_returns_fail_open():
    agent = _agent()
    for bad in ([], "not-a-list", {"messages": []}, None):
        with patch("hermes_cli.lifecycle.has_hook", return_value=True), \
                patch("hermes_cli.lifecycle.invoke_hook", return_value=[bad]):
            out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=MagicMock())
        assert out is REQUEST


def test_raising_hook_fails_open_with_warning():
    agent = _agent()
    logger = MagicMock()
    with patch("hermes_cli.lifecycle.has_hook", return_value=True), \
            patch("hermes_cli.lifecycle.invoke_hook", side_effect=RuntimeError("boom")):
        out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=logger)
    assert out is REQUEST
    assert logger.warning.called


def test_replacement_still_carrying_persistence_fields_is_stripped():
    """A plugin echoing clones with persistence-only fields gets them stripped
    (the pre-hook request copy was already stripped; the replacement must not
    reintroduce them onto the wire)."""
    from agent.message_metadata import PERSISTENCE_ONLY_MESSAGE_FIELDS

    field = next(iter(PERSISTENCE_ONLY_MESSAGE_FIELDS))
    replacement = [
        {"role": "system", "content": "sys", field: "x"},
        {"role": "user", "content": "hello"},
    ]
    agent = _agent()
    with patch("hermes_cli.lifecycle.has_hook", return_value=True), \
            patch("hermes_cli.lifecycle.invoke_hook", return_value=[replacement]):
        out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=MagicMock())
    assert out is not replacement
    assert all(field not in m for m in out)
