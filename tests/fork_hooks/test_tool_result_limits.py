"""``resolve_tool_result_limits``: plugins set the spill threshold, preview, turn budget, sweep age and
whether the terminal spill is redacted in place (spec 04). Upstream's defaults without a callback."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from tools import budget_config, tool_result_limits as trl, tool_result_storage


def test_without_a_callback_nothing_changes(manager):
    assert trl.limits() == {}
    budget = budget_config.budget_for_context_window(None)
    assert trl.apply_budget_limits(budget, None) is budget
    assert trl.spill_age_hours(24) == 24
    assert trl.redact_spill() is True


def test_the_first_valid_value_per_key_wins(register):
    register(trl.HOOK, lambda **_: {"result_size_chars": "8192", "preview_size_chars": 0, "redact_spill": "no"})
    register(trl.HOOK, lambda **_: {"result_size_chars": 9000, "preview_size_chars": 4096, "redact_spill": False})
    register(trl.HOOK, lambda **_: {"result_size_chars": 1, "unknown": 5})
    assert trl.limits() == {"result_size_chars": 9000, "preview_size_chars": 4096, "redact_spill": False}


def test_a_raising_callback_is_skipped(register):
    def boom(**_):
        raise RuntimeError("x")
    register(trl.HOOK, boom)
    register(trl.HOOK, lambda **_: {"turn_budget_chars": 50000})
    assert trl.limits() == {"turn_budget_chars": 50000}


def test_without_a_context_the_values_are_used_as_given(register):
    register(trl.HOOK, lambda **_: {"result_size_chars": 8192, "turn_budget_chars": 4000, "preview_size_chars": 8192})
    budget = trl.apply_budget_limits(budget_config.budget_for_context_window(None), None)
    assert (budget.default_result_size, budget.turn_budget, budget.preview_size) == (8192, 4000, 8192)


def test_a_context_sized_budget_is_lowered_never_below_the_floors(register):
    register(trl.HOOK, lambda **_: {"result_size_chars": 8192, "turn_budget_chars": 1000})
    budget = trl.apply_budget_limits(budget_config.budget_for_context_window(200_000), 200_000)
    assert budget.default_result_size == 8192
    assert budget.turn_budget == budget_config._MIN_TURN_BUDGET_CHARS


def test_a_cap_above_upstreams_ceiling_does_not_raise_a_sized_budget(register):
    register(trl.HOOK, lambda **_: {"result_size_chars": 10_000_000})
    upstream = budget_config.budget_for_context_window(1_000_000)
    assert trl.apply_budget_limits(upstream, 1_000_000).default_result_size == upstream.default_result_size


def test_the_agent_budget_goes_through_the_hook(register):
    from agent.tool_executor import _budget_for_agent

    register(trl.HOOK, lambda **_: {"result_size_chars": 8192, "preview_size_chars": 8192})
    agent = SimpleNamespace(context_compressor=SimpleNamespace(context_length=200_000))
    budget = _budget_for_agent(agent)
    assert (budget.default_result_size, budget.preview_size) == (8192, 8192)


def test_the_sweep_age_is_the_plugins_never_below_a_day(register):
    register(trl.HOOK, lambda **_: {"spillover_max_age_hours": 2})
    assert trl.spill_age_hours(48) == tool_result_storage.SPILLOVER_MAX_AGE_HOURS


def test_the_sweep_keeps_a_spill_younger_than_the_plugins_age(register, tmp_path, monkeypatch):
    register(trl.HOOK, lambda **_: {"spillover_max_age_hours": 168})
    monkeypatch.setattr(tool_result_storage, "get_spillover_dir", lambda: tmp_path)
    monkeypatch.setattr(tool_result_storage, "referenced_spillover_names", lambda spill_dir: set())
    spill = tmp_path / "old.txt"
    spill.write_text("x")
    old = time.time() - 72 * 3600
    os.utime(spill, (old, old))
    assert tool_result_storage.cleanup_spillover_cache(max_age_hours=24) == 0
    assert spill.exists()


def test_redact_spill_false_leaves_the_spill_file_raw(register, tmp_path):
    from tools.terminal_tool_result import _redact_spill_file

    register(trl.HOOK, lambda **_: {"redact_spill": False})
    spill = tmp_path / "spill.txt"
    raw = "\x1b[31mOPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789\x1b[0m\n"
    spill.write_text(raw)
    handle = dict(_redact_spill_file(str(spill), len(raw), "env"))
    assert handle["full_output_path"] == str(spill)
    assert spill.read_text() == raw


def test_by_default_the_spill_file_is_redacted(manager, tmp_path):
    from tools.terminal_tool_result import _redact_spill_file

    spill = tmp_path / "spill.txt"
    raw = "\x1b[31mtext\x1b[0m\n"
    spill.write_text(raw)
    _redact_spill_file(str(spill), len(raw), "echo")
    assert spill.read_text() != raw  # ANSI stripped by the redaction pass
