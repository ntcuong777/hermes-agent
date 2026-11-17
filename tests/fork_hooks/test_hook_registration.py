"""The fork hooks of spec 04 are valid hook names, and the write policy fails closed like pre_tool_call."""

from __future__ import annotations

import inspect

from hermes_cli import plugins, plugins_dispatch

HOOKS = ("resolve_write_policy", "resolve_tool_result_limits", "resolve_reply_max_tokens")


def test_the_three_hooks_are_valid():
    assert set(HOOKS) <= plugins.VALID_HOOKS


def test_only_the_write_policy_fails_closed():
    assert "resolve_write_policy" in plugins_dispatch._HOOK_TIMEOUT_FAIL_CLOSED_HOOKS
    assert "resolve_tool_result_limits" not in plugins_dispatch._HOOK_TIMEOUT_FAIL_CLOSED_HOOKS
    assert "resolve_reply_max_tokens" not in plugins_dispatch._HOOK_TIMEOUT_FAIL_CLOSED_HOOKS


def test_the_old_fork_code_is_gone_from_core():
    from agent import agent_init, chat_completion_helpers
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    from tools import budget_config, file_tools_write_guards, tool_output_limits

    assert not hasattr(file_tools_write_guards, "_system_path_allow_roots")
    assert not hasattr(budget_config, "configured_budget_int")
    assert not hasattr(tool_output_limits, "redact_spill_enabled")
    assert not hasattr(chat_completion_helpers, "_reply_capped_max_tokens")
    assert "reply_max_tokens" not in agent_init._SESSION_STATE
    assert "write_allow_roots" not in DEFAULT_CONFIG["security"]
    assert "reply_max_tokens" not in DEFAULT_CONFIG["agent"]


def test_each_call_site_is_one_line():
    from agent import chat_completion_helpers, tool_executor
    from tools import file_tools_write_guards, terminal_tool_result, tool_result_storage

    def count(module, needle):
        return inspect.getsource(module).count(needle)

    assert count(file_tools_write_guards, "write_path_policy.system_path_denied(") == 1
    assert count(tool_executor, "tool_result_limits.apply_budget_limits(") == 1
    assert count(tool_result_storage, "tool_result_limits.spill_age_hours(") == 1
    assert count(terminal_tool_result, "tool_result_limits.redact_spill()") == 1
    assert count(chat_completion_helpers, "reply_cap.capped(agent, agent.max_tokens)") == 5
