"""Tests for the ``transform_request_messages`` plugin hook apply site.

Egress backstop (task 06 / spec step 2.2): the hook rewrites ONLY the outgoing
provider request copy, at the same seam as ``ContextEngine.select_context``
results. Fail-open on exceptions and invalid returns, persisted history never
mutated (the hook receives structural clones), the no-plugin path is
byte-identical (gated on ``has_hook`` before any copy is made).

The callbacks are CHAINED (review T1): each accepted output feeds the next, so a
persona insertion and the redactor's backstop both reach the wire. Treating the
first valid list as the winner let the persona rewrite — which resolves on
ordinary requests — silently discard the redactor's rewrite.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from agent.conversation_loop import _apply_transform_request_messages_plugins


REQUEST = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hello"},
]

#: Callback registry the composition test drives through the mocked chain entry point.
_CALLBACKS: list = []


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
            patch("hermes_cli.plugins.invoke_hook_chained") as _ih:
        out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=logger)
    assert out is REQUEST
    _hh.assert_called_once_with("transform_request_messages")
    _ih.assert_not_called()
    assert not logger.warning.called


def test_an_accepted_rewrite_is_applied():
    agent = _agent()
    replacement = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "REDACTED"},
    ]
    seen = {}

    def _hook(hook_name, chain_key, **kwargs):
        seen.update(kwargs)
        seen["chain_key"] = chain_key
        # An invalid return is refused, the next accepted one lands.
        return replacement

    with patch("hermes_cli.lifecycle.has_hook", return_value=True), \
            patch("hermes_cli.plugins.invoke_hook_chained", side_effect=_hook):
        out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=MagicMock())
    assert out is replacement
    assert [m["content"] for m in seen["messages"]] == ["sys", "hello"]
    assert seen["chain_key"] == "messages"
    assert seen["session_id"] == "test-session"
    assert seen["model"] == "test-model"


def test_hook_receives_clones_persisted_history_safe():
    """A hook mutating the list it received must not reach the caller's request."""
    agent = _agent()

    def _hook(hook_name, chain_key, **kwargs):
        kwargs["messages"][0]["content"] = "TAMPERED"
        kwargs["messages"].append({"role": "user", "content": "INJECTED"})
        return None

    with patch("hermes_cli.lifecycle.has_hook", return_value=True), \
            patch("hermes_cli.plugins.invoke_hook_chained", side_effect=_hook):
        out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=MagicMock())
    assert out is REQUEST
    assert REQUEST == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]


def test_invalid_returns_fail_open():
    agent = _agent()
    # ``invoke_hook_chained`` returns the value unchained when nothing is accepted, so
    # the apply site sees a rejected/absent rewrite: the original list, or junk.
    for bad in ([], "not-a-list", {"messages": []}, None, REQUEST):
        def _hook(hook_name, chain_key, _bad=bad, **kwargs):
            # The chain hands back what it started with when accept refuses _bad.
            return kwargs[chain_key] if _bad is REQUEST else _bad

        with patch("hermes_cli.lifecycle.has_hook", return_value=True), \
                patch("hermes_cli.plugins.invoke_hook_chained", side_effect=_hook):
            out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=MagicMock())
        assert out is REQUEST


def test_raising_hook_fails_open_with_warning():
    agent = _agent()
    logger = MagicMock()
    with patch("hermes_cli.lifecycle.has_hook", return_value=True), \
            patch("hermes_cli.plugins.invoke_hook_chained", side_effect=RuntimeError("boom")):
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
            patch("hermes_cli.plugins.invoke_hook_chained", return_value=replacement):
        out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=MagicMock())
    assert out is not replacement
    assert all(field not in m for m in out)


def test_the_chain_composes_two_rewriters():
    """The regression: a plugin that rewrites can never see an earlier plugin's rewrite
    when results are collected independently, so the second rewrite was discarded."""
    agent = _agent()
    calls = []

    def _hook(hook_name, chain_key, **kwargs):
        """Drive the real chain, not a mock of it: the callbacks the manager holds."""
        current = kwargs[chain_key]
        for callback in _CALLBACKS:
            ret = callback(**{**kwargs, chain_key: current})
            if _is_request_message_list(ret):
                calls.append([m["content"] for m in ret])
                current = ret
        return current

    from agent.conversation_loop import _is_request_message_list

    seen_by_second = {}

    def persona(messages=None, **_):
        return [*messages, {"role": "system", "content": "PERSONA"}]

    def redactor(messages=None, **_):
        seen_by_second["input"] = [m["content"] for m in messages]
        return [{**m, "content": m["content"].replace("hello", "«secret»")} for m in messages]

    _CALLBACKS[:] = [persona, redactor]
    try:
        with patch("hermes_cli.lifecycle.has_hook", return_value=True), \
                patch("hermes_cli.plugins.invoke_hook_chained", side_effect=_hook):
            out = _apply_transform_request_messages_plugins(agent, REQUEST, logger=MagicMock())
    finally:
        _CALLBACKS.clear()
    assert [m["content"] for m in out] == ["sys", "«secret»", "PERSONA"]
    assert seen_by_second["input"] == ["sys", "hello", "PERSONA"], \
        "the redactor must see the persona insertion"
