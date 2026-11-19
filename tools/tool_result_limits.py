"""``resolve_tool_result_limits``: plugins set the tool-result spill limits (fork-owned).

One hook, called (without arguments beyond ``context_length``) where the limits are used:

* ``agent/tool_executor.py::_budget_for_agent``: :func:`apply_budget_limits` on upstream's
  ``budget_for_context_window`` result (``result_size_chars``, ``turn_budget_chars``,
  ``preview_size_chars``);
* ``tools/tool_result_storage.py::cleanup_spillover_cache``: :func:`spill_age_hours`
  (``spillover_max_age_hours``);
* ``tools/terminal_tool_result.py::_redact_spill_file``: :func:`redact_spill` (``redact_spill``).

A callback returns ``None`` or a dict with any of those keys. Sizes and the age are positive ints, and
``redact_spill`` is a bool; anything else is ignored. Any plugin can register the hook, so the most
conservative answer wins: for each size or age the SMALLEST valid value across the callbacks, and
``redact_spill: false`` only when the ``config-seams`` plugin says it (``tools/hook_sources.py``; a
``false`` from another plugin is ignored, ``true`` is the default anyway). Fail-open: a callback that
raises is reported and skipped by the plugin manager, and with no valid value upstream's default
stands (``redact_spill`` True); so does any error in this module.

Semantics: ``result_size_chars`` / ``turn_budget_chars`` lower upstream's ceilings (a context-sized
budget keeps upstream's 8,000 / 16,000 floors; a budget without a known context uses the value as
given); ``preview_size_chars`` replaces the preview size; ``spillover_max_age_hours`` replaces the
sweep age, never below upstream's ``SPILLOVER_MAX_AGE_HOURS`` (24); ``redact_spill: false`` leaves the
terminal spill file as the collector wrote it.
"""

from __future__ import annotations

import dataclasses
import importlib
from typing import Any, Iterable, Mapping, Optional

from tools.hook_sources import attributed

HOOK = "resolve_tool_result_limits"
INT_KEYS = ("result_size_chars", "turn_budget_chars", "preview_size_chars", "spillover_max_age_hours")
BOOL_KEYS = ("redact_spill",)


def _valid(key: str, value: Any) -> bool:
    if key in BOOL_KEYS:
        return isinstance(value, bool)
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def merge(results: Iterable[tuple[bool, Any]]) -> dict[str, Any]:
    """The merged ``(from the trusted plugin, value)`` results: the smallest valid value per int key,
    ``redact_spill: False`` only from the trusted plugin."""
    rows = [(trusted, r) for trusted, r in (results or ()) if isinstance(r, Mapping)]
    smallest = {key: min(r[key] for _, r in rows if key in r and _valid(key, r[key]))
                for key in INT_KEYS if any(key in r and _valid(key, r[key]) for _, r in rows)}
    unredacted = any(trusted and r.get("redact_spill") is False for trusted, r in rows)
    return {**smallest, **({"redact_spill": False} if unredacted else {})}


def limits(context_length: Optional[int] = None) -> dict[str, Any]:
    """The merged plugin limits, ``{}`` when no plugin answers (or the plugin system is absent)."""
    try:
        from hermes_cli.plugins import has_hook

        if not has_hook(HOOK):
            return {}
        return merge(attributed(HOOK, context_length=context_length))
    except Exception:  # health: allow BLE001 -- fail open: upstream's limits
        return {}


def apply_budget_limits(budget: Any, context_length: Optional[int] = None) -> Any:
    """``budget`` (a ``tools.budget_config.BudgetConfig``) with the plugin limits applied; ``budget``
    itself on any error (the call site's fallback would otherwise drop upstream's MCP threshold)."""
    try:
        return _with_limits(budget, limits(context_length), context_length)
    except Exception:  # health: allow BLE001 -- fail open: upstream's budget
        return budget


def _with_limits(budget: Any, found: Mapping[str, Any], context_length: Optional[int]) -> Any:
    if not {"result_size_chars", "turn_budget_chars", "preview_size_chars"} & set(found):
        return budget
    config = importlib.import_module("tools.budget_config")
    sized = bool(context_length) and context_length > 0

    def capped(current: int, key: str, floor: int) -> int:
        if key not in found:
            return current
        return max(floor, min(current, found[key])) if sized else found[key]

    return dataclasses.replace(
        budget,
        default_result_size=capped(budget.default_result_size, "result_size_chars", config._MIN_RESULT_SIZE_CHARS),
        turn_budget=capped(budget.turn_budget, "turn_budget_chars", config._MIN_TURN_BUDGET_CHARS),
        preview_size=found.get("preview_size_chars", budget.preview_size),
    )


def spill_age_hours(default_hours: Any) -> Any:
    """The spill sweep age: the plugin's (at least 24 h) when set, else ``default_hours``."""
    configured = limits().get("spillover_max_age_hours")
    if not configured:
        return default_hours
    floor = importlib.import_module("tools.tool_result_storage").SPILLOVER_MAX_AGE_HOURS
    return max(floor, configured)


def redact_spill() -> bool:
    """Whether the terminal spill file is redacted in place (upstream: always)."""
    return limits().get("redact_spill", True)
