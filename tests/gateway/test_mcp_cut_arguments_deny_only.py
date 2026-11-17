"""An MCP call whose arguments were cut even in the structured context (``mcp.arguments_cut``) is Deny-only on
EVERY surface, not just button cards: the wire contract carries the flag (a ``Params`` model refuses unknown keys, so
the call used to be declined with no card at all), the offered choices are only ``deny``, the typed ``/approve``
text path and the canonical respond refuse any approving answer, and the text fallback offers only ``deny``.
"""
import threading

import pytest

CUT = {"server": "files", "tool": "write", "arguments": '{"a": 1}\n... [arguments truncated]', "arguments_cut": True}
WHOLE = {"server": "files", "tool": "write", "arguments": '{"a": 1}'}


def test_the_legacy_approval_request_with_a_cut_context_passes_the_wire_contract():
    from tui_gateway import server
    from tui_gateway.contracts import registry as contracts

    payload = server._approval_request_payload(
        {"request_id": "r1", "command": "c", "description": "d", "mcp": dict(CUT)})
    _, problem = contracts.validate_params(contracts.SERVER_REQUESTS["approval"], {"session_id": "s", **payload})
    assert not problem, problem
    assert payload["choices"] == ["deny"]
    whole = server._approval_request_payload({"request_id": "r2", "command": "c", "description": "d", "mcp": dict(WHOLE)})
    assert "once" in whole["choices"]


def test_the_canonical_prompt_offers_only_deny_for_a_cut_call():
    from gateway.session_pending_controls import PendingControls

    published = []

    class _Events:
        lock = threading.RLock()

        def publish(self, session_id, prompt, event_type=None):
            published.append(prompt)

    controls = PendingControls.__new__(PendingControls)
    controls.events, controls.pending, controls.idle, controls.remote_responders = _Events(), {}, set(), {}
    controls._settle_when_answered = lambda *a, **k: None
    controls.register("s", "route", 1, {"request_id": "p1", "command": "c", "description": "d", "mcp": dict(CUT)})
    controls.register("s", "route", 1, {"request_id": "p2", "command": "c", "description": "d", "mcp": dict(WHOLE)})
    assert published[0]["choices"] == ["deny"] and published[0]["mcp"]["arguments_cut"] is True
    assert "once" in published[1]["choices"]


def _queue(mcp, request_id="r1"):
    from tools import approval
    from tools.approval_gateway_wait import _ApprovalEntry

    entry = _ApprovalEntry({"request_id": request_id, "command": "c", "description": "d", "mcp": dict(mcp)})
    approval._gateway_queues["sk-cut"] = [entry]
    return approval, entry


@pytest.fixture(autouse=True)
def _clean_queue():
    yield
    from tools import approval

    approval._gateway_queues.pop("sk-cut", None)


@pytest.mark.parametrize("choice", ["once", "session", "always", "approve"])
def test_no_approving_answer_resolves_a_cut_call_from_any_path(choice):
    approval, entry = _queue(CUT)
    assert approval.resolve_gateway_approval("sk-cut", choice) == 0  # typed /approve (oldest)
    assert approval.resolve_gateway_approval("sk-cut", choice, request_id="r1") == 0  # a card or viewer, by id
    assert approval.resolve_gateway_approval("sk-cut", choice, resolve_all=True) == 0  # /approve all
    assert entry.result is None and not entry.event.is_set() and approval._gateway_queues["sk-cut"] == [entry]


def test_a_cut_call_can_still_be_denied_and_a_whole_one_approved():
    approval, entry = _queue(CUT)
    assert approval.resolve_gateway_approval("sk-cut", "deny", request_id="r1") == 1
    assert entry.result == "deny"
    approval, entry = _queue(WHOLE)
    assert approval.resolve_gateway_approval("sk-cut", "once", request_id="r1") == 1
    assert entry.result == "once"


def test_approve_all_leaves_the_cut_call_pending_and_resolves_the_rest():
    from tools import approval
    from tools.approval_gateway_wait import _ApprovalEntry

    cut = _ApprovalEntry({"request_id": "a", "command": "c", "description": "d", "mcp": dict(CUT)})
    ok = _ApprovalEntry({"request_id": "b", "command": "c", "description": "d", "mcp": dict(WHOLE)})
    approval._gateway_queues["sk-cut"] = [cut, ok]
    assert approval.resolve_gateway_approval("sk-cut", "once", resolve_all=True) == 1
    assert (cut.result, ok.result) == (None, "once")
    assert approval._gateway_queues["sk-cut"] == [cut]


def test_the_text_fallback_for_a_cut_call_offers_only_deny():
    from gateway.run import _format_exec_approval_fallback

    text = _format_exec_approval_fallback("MCP tool wants to run", "MCP call", "/", mcp=dict(CUT))
    assert "deny" in text and "approve" not in text.replace("/deny", "")
    whole = _format_exec_approval_fallback("MCP tool wants to run", "MCP call", "/", mcp=dict(WHOLE))
    assert "approve" in whole
