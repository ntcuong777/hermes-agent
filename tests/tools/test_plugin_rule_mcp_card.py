"""A plugin-escalated MCP tool call must not be a blind approval.

``permission-gate``-style plugins approve every ``mcp__*`` call as a class with a
reason that names the tool alone; ``request_tool_approval`` used to forward only
that reason, so the approval card showed ``<mcp__server__tool> (plugin approval
rule)`` with none of the request's content. The call's arguments now ride into
the gate and reach the wire contract's structured ``mcp`` card (the same shape
the MCP trust gate sends), redacted by the same machinery.
"""

import pytest

from tools import approval
from tools import approval_prompt
import tools.approval_context as tools_approval_context
from tools.approval import request_tool_approval


@pytest.fixture(autouse=True)
def _isolate_approval_state(monkeypatch):
    monkeypatch.setattr(
        approval, "get_current_session_key", lambda default="default": "test-session"
    )
    monkeypatch.setattr(
        tools_approval_context,
        "get_current_session_key",
        lambda default="default": "test-session",
    )
    monkeypatch.setattr(approval, "is_approved", lambda sk, pk: False)
    monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: False)
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", False, raising=False)
    monkeypatch.setattr(
        "tools.terminal_tool._get_approval_callback", lambda: None, raising=False
    )
    yield


def _gateway_context(monkeypatch):
    monkeypatch.setattr(approval, "_is_interactive_cli", lambda: False)
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: True)
    monkeypatch.setattr(
        tools_approval_context, "_is_gateway_approval_context", lambda: True
    )
    monkeypatch.setattr(approval, "_is_cron_approval_context", lambda: False)
    monkeypatch.setattr(
        tools_approval_context, "_is_cron_approval_context", lambda: False
    )


def _answer_once(monkeypatch, notified):
    def approve(data):
        notified.append(data)
        assert (
            approval.resolve_gateway_approval(
                "test-session", "once", request_id=data["request_id"]
            )
            == 1
        )

    approval.register_gateway_notify("test-session", approve)
    return approve


def test_plugin_rule_mcp_call_carries_redacted_arguments_card(monkeypatch):
    _gateway_context(monkeypatch)
    notified = []
    _answer_once(monkeypatch, notified)
    try:
        res = request_tool_approval(
            "mcp__prod_core_db__get_query_plan",
            "MCP tool call (mcp__prod_core_db__get_query_plan) leaves the local sandbox and requires approval.",
            args={"sql": "select count(*) from users", "db_password": "hunter2"},
        )
    finally:
        approval.unregister_gateway_notify("test-session")

    assert res["approved"] is True
    card = notified[0]["mcp"]
    assert card["server"] == "prod_core_db"
    assert card["tool"] == "get_query_plan"
    # The call's content is on the card…
    assert "select count(*) from users" in card["arguments"]
    # …while a credential-shaped field is masked by the same machinery the trust gate uses.
    assert "hunter2" not in card["arguments"]
    assert '"***"' in card["arguments"]


def test_non_mcp_plugin_rule_keeps_command_chrome(monkeypatch):
    _gateway_context(monkeypatch)
    notified = []
    _answer_once(monkeypatch, notified)
    try:
        res = request_tool_approval(
            "terminal", "smtp send", args={"command": "sendmail -t"}
        )
    finally:
        approval.unregister_gateway_notify("test-session")

    assert res["approved"] is True
    assert "mcp" not in notified[0]


def test_mcp_call_without_args_degrades_to_the_old_shape(monkeypatch):
    _gateway_context(monkeypatch)
    notified = []
    _answer_once(monkeypatch, notified)
    try:
        res = request_tool_approval(
            "mcp__prod_core_db__get_query_plan", "leaves the local sandbox"
        )
    finally:
        approval.unregister_gateway_notify("test-session")

    assert res["approved"] is True
    assert "mcp" not in notified[0]
