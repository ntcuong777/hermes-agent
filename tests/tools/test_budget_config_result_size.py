"""`tool_budget:` in config.yaml sets the spill threshold for every tool, not only MCP.

Only ``mcp_result_size_chars`` was configurable, so a non-MCP tool result stayed inline up to
100 000 chars before it was written to ``cache/spillover`` and replaced by a preview. These keys
let a setup choose a lower cap (the personal fork uses 16 KB, like Pi's output-cap):

    tool_budget:
      result_size_chars: 16384        # per-result threshold for every tool except read_file
      preview_size_chars: 16384       # how much of a spilled result stays inline
      turn_budget_chars: 65536        # per-turn aggregate
      spillover_max_age_hours: 168    # how long spill files live
"""

import os
import time

import pytest

from tools.budget_config import (
    DEFAULT_BUDGET,
    DEFAULT_PREVIEW_SIZE_CHARS,
    DEFAULT_RESULT_SIZE_CHARS,
    DEFAULT_TURN_BUDGET_CHARS,
    budget_for_context_window,
)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def write(body: str):
        (tmp_path / "config.yaml").write_text(body)
        return tmp_path

    return write


def test_result_size_applies_to_any_tool_without_a_window(config_home):
    config_home("tool_budget:\n  result_size_chars: 16384\n")
    budget = budget_for_context_window(None)
    assert budget.default_result_size == 16_384
    assert budget.resolve_threshold("search_files") == 16_384
    assert budget.resolve_threshold("execute_code") == 16_384


def test_result_size_applies_with_a_large_window(config_home):
    config_home("tool_budget:\n  result_size_chars: 16384\n")
    budget = budget_for_context_window(200_000)
    assert budget.default_result_size == 16_384
    assert budget.resolve_threshold("web_extract") == 16_384


def test_a_small_window_still_caps_a_larger_configured_size(config_home):
    config_home("tool_budget:\n  result_size_chars: 500000\n")
    budget = budget_for_context_window(16_384)
    assert budget.default_result_size < 500_000


def test_preview_and_turn_budget_are_configurable(config_home):
    config_home(
        "tool_budget:\n  preview_size_chars: 9000\n  turn_budget_chars: 70000\n"
    )
    for window in (None, 200_000):
        budget = budget_for_context_window(window)
        assert budget.preview_size == 9_000
        assert budget.turn_budget == 70_000


def test_read_file_stays_unbounded_by_the_result_size(config_home):
    config_home("tool_budget:\n  result_size_chars: 16384\n")
    assert budget_for_context_window(None).resolve_threshold("read_file") == float(
        "inf"
    )


@pytest.mark.parametrize("bad", ["0", "-5", "lots", "[]", "null"])
def test_unusable_values_fall_back_to_the_defaults(config_home, bad):
    config_home(
        f"tool_budget:\n  result_size_chars: {bad}\n  preview_size_chars: {bad}\n  turn_budget_chars: {bad}\n"
    )
    budget = budget_for_context_window(None)
    assert budget is DEFAULT_BUDGET
    assert budget.default_result_size == DEFAULT_RESULT_SIZE_CHARS
    assert budget.preview_size == DEFAULT_PREVIEW_SIZE_CHARS
    assert budget.turn_budget == DEFAULT_TURN_BUDGET_CHARS


def _old_spill(home, hours_old: float):
    spill = home / "cache" / "spillover"
    spill.mkdir(parents=True)
    path = spill / "old.txt"
    path.write_text("x")
    stamp = time.time() - hours_old * 3600
    os.utime(path, (stamp, stamp))
    return path


def test_spillover_keeps_files_for_the_configured_hours(config_home):
    home = config_home("tool_budget:\n  spillover_max_age_hours: 168\n")
    path = _old_spill(home, hours_old=30)
    from tools.tool_result_storage import cleanup_spillover_cache

    assert cleanup_spillover_cache() == 0
    assert path.exists()


def test_spillover_default_keeps_a_spill_for_the_session_retention_window(config_home):
    """An unreferenced spill is kept for the session retention window by default (#126351), not a day."""
    home = config_home("terminal:\n  backend: local\n")
    path = _old_spill(home, hours_old=30)
    from tools.tool_result_storage import cleanup_spillover_cache

    assert cleanup_spillover_cache() == 0
    assert path.exists()


def test_a_configured_sub_day_spill_age_is_floored_at_a_day(config_home):
    """A one-hour override must not cut off a spill the first write just made (the pointer would name a missing file)."""
    home = config_home("tool_budget:\n  spillover_max_age_hours: 1\n")
    path = _old_spill(home, hours_old=2)
    from tools.tool_result_storage import cleanup_spillover_cache

    assert cleanup_spillover_cache() == 0
    assert path.exists()


def test_the_configured_age_wins_over_the_callers_age(config_home):
    """The gateway's hourly sweep passes the media cache age; the setup's choice must still hold."""
    home = config_home("tool_budget:\n  spillover_max_age_hours: 168\n")
    path = _old_spill(home, hours_old=30)
    from tools.tool_result_storage import cleanup_spillover_cache

    assert cleanup_spillover_cache(max_age_hours=24) == 0
    assert path.exists()


def test_the_callers_age_is_used_when_nothing_is_configured(config_home):
    home = config_home("terminal:\n  backend: local\n")
    path = _old_spill(home, hours_old=30)
    from tools.tool_result_storage import cleanup_spillover_cache

    assert cleanup_spillover_cache(max_age_hours=48) == 0
    assert cleanup_spillover_cache(max_age_hours=24) == 1
    assert not path.exists()
