"""Tests for tools.approval.request_tool_approval — the plugin pre_tool_call
``{"action": "approve"}`` escalation into the human-approval gate.

These verify that a plugin-driven approval reuses the SAME machinery as a
Tier-2 dangerous-command match: session/permanent allowlist, the CLI prompt,
the gateway pending path, cron_mode, and fail-closed timeouts.
"""

import contextvars

import pytest

from tools import approval
from tools import approval_prompt
import tools.approval_context as tools_approval_context
from tools import approval_context
from tools.approval import request_tool_approval


def _api_server_plugin_request():
    from gateway.session_context import set_session_vars
    set_session_vars(platform="api_server")
    return request_tool_approval("home_lock", "unlock the front door", rule_key="unlock")


@pytest.fixture(autouse=True)
def _isolate_approval_state(monkeypatch):
    """Give each test a clean session key and empty allowlists."""
    monkeypatch.setattr(
        approval, "get_current_session_key",
        lambda default="default": "test-session",
    )
    monkeypatch.setattr(
        tools_approval_context, "get_current_session_key",
        lambda default="default": "test-session",
    )
    # Empty session + permanent approval stores so nothing pre-approves.
    monkeypatch.setattr(approval, "is_approved", lambda sk, pk: False)
    # Not a yolo session (the shared gate checks this first).
    monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: False)
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", False, raising=False)
    # No thread-registered CLI callback by default.
    monkeypatch.setattr(
        "tools.terminal_tool._get_approval_callback", lambda: None, raising=False
    )
    yield


class TestRequestToolApproval:
    def test_session_cached_approval_short_circuits(self, monkeypatch):
        monkeypatch.setattr(approval, "is_approved", lambda sk, pk: True)
        # Should NOT prompt at all.
        monkeypatch.setattr(
            approval, "prompt_dangerous_approval",
            lambda *a, **k: pytest.fail("should not prompt when already approved"),
        )
        monkeypatch.setattr(
            approval_prompt, "prompt_dangerous_approval",
            lambda *a, **k: pytest.fail("should not prompt when already approved"),
        )
        res = request_tool_approval("write_file", "sensitive path", rule_key="ssh")
        assert res == {"approved": True, "message": None}

    def test_cli_approve_once(self, monkeypatch):
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "prompt_dangerous_approval", lambda *a, **k: "once")
        monkeypatch.setattr(approval_prompt, "prompt_dangerous_approval", lambda *a, **k: "once")
        res = request_tool_approval("write_file", "writing ~/.ssh/authorized_keys")
        assert res["approved"] is True

    def test_cli_deny_blocks(self, monkeypatch):
        from hermes_cli import lifecycle

        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "prompt_dangerous_approval", lambda *a, **k: "deny")
        monkeypatch.setattr(approval_prompt, "prompt_dangerous_approval", lambda *a, **k: "deny")
        events = []
        monkeypatch.setattr(
            lifecycle,
            "invoke_hook",
            lambda hook_name, **kwargs: events.append((hook_name, kwargs)) or [],
        )
        tokens = approval_context.set_current_observability_context(
            turn_id="turn-1",
            tool_call_id="call-1",
        )
        try:
            res = request_tool_approval("terminal", "curl PUT to external API")
        finally:
            approval_context.reset_current_observability_context(tokens)
        assert res["approved"] is False
        assert "denied" in res["message"].lower()
        assert res["pattern_key"].startswith("plugin_rule:")
        assert [name for name, _ in events] == [
            "pre_approval_request",
            "post_approval_response",
        ]
        assert all(event["turn_id"] == "turn-1" for _, event in events)
        assert all(event["tool_call_id"] == "call-1" for _, event in events)
        assert events[-1][1]["choice"] == "deny"

    def test_cli_session_persists_session_only(self, monkeypatch):
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "prompt_dangerous_approval", lambda *a, **k: "session")
        monkeypatch.setattr(approval_prompt, "prompt_dangerous_approval", lambda *a, **k: "session")
        calls = {"session": [], "permanent": []}
        monkeypatch.setattr(approval, "approve_session",
                            lambda sk, pk: calls["session"].append(pk))
        monkeypatch.setattr(approval, "approve_permanent",
                            lambda pk: calls["permanent"].append(pk))
        monkeypatch.setattr(approval, "save_permanent_allowlist", lambda x: None)
        res = request_tool_approval("write_file", "reason", rule_key="ssh-writes")
        assert res["approved"] is True
        assert calls["session"] == ["plugin_rule:ssh-writes"]
        assert calls["permanent"] == []  # session != always


    def test_cron_deny_mode_blocks(self, monkeypatch):
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "_is_cron_approval_context", lambda: True)
        monkeypatch.setattr(tools_approval_context, "_is_cron_approval_context", lambda: True)
        monkeypatch.setattr(approval_context, "_get_cron_approval_mode", lambda: "deny")
        res = request_tool_approval("terminal", "smtp send")
        assert res["approved"] is False
        assert "cron" in res["message"].lower()

    def test_cron_approve_mode_allows(self, monkeypatch):
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "_is_cron_approval_context", lambda: True)
        monkeypatch.setattr(tools_approval_context, "_is_cron_approval_context", lambda: True)
        monkeypatch.setattr(approval_context, "_get_cron_approval_mode", lambda: "approve")
        res = request_tool_approval("terminal", "smtp send")
        assert res["approved"] is True


    def test_distinct_reasons_get_distinct_keys(self, monkeypatch):
        """Two different reasons on the SAME tool must not share an [a]lways
        allowlist entry (Finding 3: tool_name alone was too coarse)."""
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "prompt_dangerous_approval", lambda *a, **k: "deny")
        monkeypatch.setattr(approval_prompt, "prompt_dangerous_approval", lambda *a, **k: "deny")
        k1 = request_tool_approval("write_file", "write to ~/.ssh")["pattern_key"]
        k2 = request_tool_approval("write_file", "send an email")["pattern_key"]
        assert k1 != k2

    def test_explicit_rule_key_overrides_derivation(self, monkeypatch):
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "prompt_dangerous_approval", lambda *a, **k: "deny")
        monkeypatch.setattr(approval_prompt, "prompt_dangerous_approval", lambda *a, **k: "deny")
        res = request_tool_approval("terminal", "any", rule_key="my-rule")
        assert res["pattern_key"] == "plugin_rule:my-rule"

    def test_no_human_non_cron_fails_closed(self, monkeypatch):
        """Non-interactive, non-gateway, NON-cron context blocks (fail-closed)
        — a plugin-flagged action never runs ungated without a human."""
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_cron_approval_context", lambda: False)
        res = request_tool_approval("terminal", "smtp send")
        assert res["approved"] is False
        assert "no interactive user or gateway" in res["message"].lower()

    def test_api_server_exec_ask_uses_approval_bridge(self, monkeypatch):
        """Plugin escalation reaches the /v1/runs approval bridge when ask mode is active."""
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(approval, "_is_single_query_approval_context", lambda: False)
        monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
        monkeypatch.setenv("HERMES_EXEC_ASK", "1")
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "api_server")

        notified = []

        def approve(data):
            notified.append(data)
            assert approval.resolve_gateway_approval(
                "test-session", "once", request_id=data["request_id"]
            ) == 1

        approval.register_gateway_notify("test-session", approve)
        try:
            res = request_tool_approval("home_lock", "unlock the front door", rule_key="unlock")
        finally:
            approval.unregister_gateway_notify("test-session")

        assert res["approved"] is True
        assert len(notified) == 1
        assert notified[0]["pattern_key"] == "plugin_rule:unlock"

    def test_api_server_without_exec_ask_remains_fail_closed(self, monkeypatch):
        """An api_server call without an active approval bridge must not run ungated."""
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(approval, "_is_single_query_approval_context", lambda: False)
        monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
        monkeypatch.setattr(approval_context, "_get_unattended_approval_mode", lambda: "deny")
        monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "api_server")

        # The gateway binds the platform per turn. A session context cleared by an earlier test holds "" and
        # would mask the env var, so the turn runs in its own context with the platform bound.
        res = contextvars.copy_context().run(_api_server_plugin_request)

        assert res["approved"] is False
        assert "unattended platform" in res["message"].lower()

    def test_yolo_session_bypasses_gate(self, monkeypatch):
        """A --yolo session skips the plugin approval gate (parity with the
        dangerous-command path, via the shared _run_approval_gate)."""
        monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: True)
        monkeypatch.setattr(
            approval, "prompt_dangerous_approval",
            lambda *a, **k: pytest.fail("yolo must not prompt"),
        )
        monkeypatch.setattr(
            approval_prompt, "prompt_dangerous_approval",
            lambda *a, **k: pytest.fail("yolo must not prompt"),
        )
        res = request_tool_approval("terminal", "curl PUT", rule_key="ext")
        assert res == {"approved": True, "message": None}

    def test_approvals_mode_off_bypasses_gate(self, monkeypatch):
        """``approvals.mode: off`` (the Desktop "Approvals: off" toggle) must bypass the shared gate
        exactly like the shell guards do — otherwise computer_use / plugin-rule / SSH-config-write
        approvals keep prompting a user who turned approvals off."""
        monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "off")
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(
            approval, "prompt_dangerous_approval",
            lambda *a, **k: pytest.fail("approvals.mode=off must not prompt"),
        )
        monkeypatch.setattr(
            approval_prompt, "prompt_dangerous_approval",
            lambda *a, **k: pytest.fail("approvals.mode=off must not prompt"),
        )
        res = request_tool_approval("computer_use", "click", rule_key="cua")
        assert res == {"approved": True, "message": None}


class TestYoloExemptPluginApproval:
    """A plugin ``approve`` marked ``yolo_exempt`` (the permission-gate's exception list: secret
    files, sudo, devices, ssh, cron, force push) must reach a human even under yolo; a routine
    approve keeps the yolo skip; a block is never softened. ``_human_decision`` is the human gate:
    it is stubbed so the tests assert the call reached it, independent of the prompt surface."""

    @pytest.fixture
    def asked(self, monkeypatch):
        calls = []

        def _human(spec, **kwargs):
            calls.append(kwargs["pattern_key"])
            return approval._denied("denied by human", pattern_key=kwargs["pattern_key"],
                                    description=kwargs["description"], outcome="denied")

        monkeypatch.setattr(approval, "_human_decision", _human)
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: True)
        monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_context, "_get_approval_mode", lambda: "manual")
        return calls

    def _resolve(self, monkeypatch, verdict):
        from hermes_cli.plugins import resolve_pre_tool_block
        monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda hook_name, **kwargs: [verdict])
        return resolve_pre_tool_block("terminal", {"command": "sudo ls"})

    def test_exempt_asks_under_session_yolo(self, monkeypatch, asked):
        monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: True)
        res = request_tool_approval("terminal", "sudo", rule_key="pg:cmd:sudo", yolo_exempt=True)
        assert asked == ["plugin_rule:pg:cmd:sudo"]
        assert res["approved"] is False

    def test_exempt_asks_under_process_yolo(self, monkeypatch, asked):
        monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", True, raising=False)
        res = request_tool_approval("terminal", "crontab", rule_key="pg:cmd:crontab", yolo_exempt=True)
        assert asked == ["plugin_rule:pg:cmd:crontab"]
        assert res["approved"] is False

    def test_routine_approve_still_skips_under_yolo(self, monkeypatch, asked):
        monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: True)
        assert request_tool_approval("terminal", "rm -rf", rule_key="pg:cmd:rm") == {"approved": True, "message": None}
        assert asked == []

    def test_exempt_under_unattended_turn_fails_closed(self, monkeypatch, asked):
        """``hermes -z`` counts as yolo; with nobody to ask, an exception blocks rather than runs."""
        from gateway import session_finite
        monkeypatch.setattr(approval, "_is_interactive_cli", lambda: False)
        monkeypatch.setattr(approval, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(tools_approval_context, "_is_cron_approval_context", lambda: False)
        monkeypatch.setattr(approval, "_is_single_query_approval_context", lambda: False)
        monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
        monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
        with session_finite.finite_turn_scope(True, unattended=True):
            assert request_tool_approval("terminal", "x", rule_key="k")["approved"] is True
            res = request_tool_approval("terminal", "ssh", rule_key="pg:cmd:ssh", yolo_exempt=True)
        assert res["approved"] is False

    def test_plugin_exempt_verdict_prompts_under_session_yolo(self, monkeypatch, asked):
        monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: True)
        msg = self._resolve(monkeypatch, {"action": "approve", "message": "sudo", "rule_key": "pg:cmd:sudo",
                                          "yolo_exempt": True})
        assert asked == ["plugin_rule:pg:cmd:sudo"]
        assert msg == "denied by human"

    def test_plugin_exempt_verdict_prompts_under_process_yolo(self, monkeypatch, asked):
        monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", True, raising=False)
        msg = self._resolve(monkeypatch, {"action": "approve", "message": "secret", "rule_key": "pg:secret:ab",
                                          "yolo_exempt": True})
        assert asked == ["plugin_rule:pg:secret:ab"]
        assert msg == "denied by human"

    def test_plugin_malformed_exempt_marker_still_prompts(self, monkeypatch, asked):
        """Fail closed: a non-bool marker is read as exempt, never as permission to auto-approve."""
        monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: True)
        msg = self._resolve(monkeypatch, {"action": "approve", "message": "m", "rule_key": "k",
                                          "yolo_exempt": "yes"})
        assert asked == ["plugin_rule:k"]
        assert msg == "denied by human"

    def test_plugin_routine_verdict_auto_approved_under_yolo(self, monkeypatch, asked):
        monkeypatch.setattr(approval, "is_current_session_yolo_enabled", lambda: True)
        for verdict in ({"action": "approve", "message": "m", "rule_key": "k"},
                        {"action": "approve", "message": "m", "rule_key": "k", "yolo_exempt": False}):
            assert self._resolve(monkeypatch, verdict) is None
        assert asked == []

    def test_plugin_block_unchanged_under_yolo(self, monkeypatch, asked):
        monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", True, raising=False)
        assert self._resolve(monkeypatch, {"action": "block", "message": "no"}) == "no"
        assert asked == []
