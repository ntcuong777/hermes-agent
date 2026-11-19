"""A cut MCP call is deny-only with the card plugin present, absent, skipped, and faulted.

The predicate is ``tools.approval_mcp.arguments_cut``. Approving by request id
returns 0. Deny returns 1. The builder is optional context; it cannot grant.
"""

from tools.approval_gateway_wait import _ApprovalEntry
import tools.approval as approval_module
from tools.approval_mcp import arguments_cut


def _queue(key, *entries):
    approval_module._gateway_queues[key] = list(entries)


def _cut_entry():
    mcp = {"server": "files", "tool": "write", "arguments": "cut", "arguments_cut": True}
    return _ApprovalEntry({"request_id": "cut", "command": "c", "description": "d", "mcp": mcp})


def _assert_cut_is_deny_only(key="cut-closed"):
    entry = _cut_entry()
    _queue(key, entry)
    try:
        assert arguments_cut(entry.data["mcp"]) is True
        for choice in ("once", "session", "always", "approve"):
            assert approval_module.resolve_gateway_approval(key, choice, request_id="cut") == 0
            assert entry.result is None
        assert approval_module.resolve_gateway_approval(key, "deny", request_id="cut") == 1
        assert entry.result == "deny"
    finally:
        approval_module._gateway_queues.pop(key, None)


def test_absent_a_cut_call_is_not_granted_by_id():
    from tools.approval_context_seam import describe_mcp
    assert describe_mcp("files", "write", {"q": "x" * 30_000}) is None
    _assert_cut_is_deny_only("absent")


def test_present_the_builder_sets_arguments_cut_and_the_id_is_not_granted(monkeypatch):
    import ac_describe
    ac_describe.install(monkeypatch)
    from tools.approval_context_seam import describe_mcp
    small = describe_mcp("files", "write", {"q": "small"})
    assert small is not None and "arguments_cut" not in small
    cut = describe_mcp("files", "write", {"q": "x" * 30_000})
    assert cut is not None and cut["arguments_cut"] is True
    _assert_cut_is_deny_only("present")


def test_skipped_builder_attaches_nothing_and_a_cut_payload_is_still_not_granted():
    """A skipped patch leaves ``_describe`` returning None. The predicate does not."""
    from tools.approval_context_seam import _describe, describe_mcp
    assert _describe("files", "write", {"q": "x" * 30_000}) is None
    assert describe_mcp("files", "write", {"q": "x"}) is None
    _assert_cut_is_deny_only("skipped")


def test_faulted_builder_returns_none_and_a_cut_payload_is_still_not_granted(monkeypatch):
    import tools.approval_context_seam as seam

    def boom(*_a, **_k):
        raise RuntimeError("describe fault")

    monkeypatch.setattr(seam, "_describe", boom)
    assert seam.describe_mcp("files", "write", {"q": "x" * 30_000}) is None
    _assert_cut_is_deny_only("faulted")
