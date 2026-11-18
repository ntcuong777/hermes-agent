"""Loud startup check: a lane feature the core removed is off when its serving plugin is not loaded (fork-owned).

The core removed these features from upstream-owned files (``gateway/session_lane_features.py``, ``REMOVED_FROM_CORE``)
and their plugins carry them. With the plugin absent, disabled, errored or ungranted the core silently runs without
them: pills blank, the phone's model, bound-session writes refused, new sessions in a mapped channel in the default
folder, wake notices dropped. This check says so once: one ERROR log naming the missing plugins and the features they
serve, and one home-channel notice (the critical alert's notifier and de-dup). It never raises and never blocks.

Off when: ``core_patches.warn_missing_plugin: false``; the core carries no marker (an old core, nothing removed);
the discovery did not finish within the startup budget (the answer is not known yet).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

logger = logging.getLogger("hermes_cli.core_patches")

#: The state last announced in this process: a repeated apply with the same gap says nothing again.
_ANNOUNCED: list = [None]


def missing_by_plugin(records: Iterable[Any], managers: Iterable[Any], removed: Iterable[str],
                      served_by: Mapping[str, str]) -> dict[str, list[str]]:
    """plugin -> the removed feature ids it serves that nothing serves now. A plugin serves when a loaded manager holds
    it enabled and without an error, and none of its own core patches is ``skipped-no-grant`` (ungranted)."""
    ungranted = {getattr(r.spec, "plugin_id", None) for r in records if r.status == "skipped-no-grant"}
    serving: set[str] = set()
    for manager in managers:
        for loaded in list((getattr(manager, "_plugins", None) or {}).values()):
            name = str(getattr(getattr(loaded, "manifest", None), "name", "") or "")
            if name and getattr(loaded, "enabled", False) and not getattr(loaded, "error", None):
                serving.add(name)
    serving -= ungranted
    gap: dict[str, list[str]] = {}
    for feature in sorted(removed):
        plugin = served_by.get(feature)
        if plugin is not None and plugin not in serving:
            gap.setdefault(plugin, []).append(feature)
    return gap


def report_missing_plugins(records: Iterable[Any], managers: Iterable[Any], *, timed_out: bool = False) -> dict[str, list[str]]:
    """The startup check (see the module doc). Returns the gap it found (empty when silent). Never raises."""
    try:
        from hermes_cli import core_patches_policy as policy

        if timed_out or not policy.load_settings().warn_missing_plugin:
            return {}
        from gateway.session_lane_features import REMOVED_FROM_CORE, SERVED_BY

        gap = missing_by_plugin(list(records), list(managers), REMOVED_FROM_CORE, SERVED_BY)
        if not gap:
            _ANNOUNCED[0] = None
            return {}
        lines = [f"{plugin} is not loaded or enabled; off without it: {', '.join(features)}"
                 for plugin, features in sorted(gap.items())]
        state = tuple(lines)
        if _ANNOUNCED[0] == state:
            return gap
        _ANNOUNCED[0] = state
        logger.error("core patches: lane features are not served; the core removed them and their plugin is not "
                     "loaded and enabled: %s (core_patches.warn_missing_plugin: false silences this)", "; ".join(lines))
        try:
            from hermes_cli import core_patches_report as report

            report.notify_missing_plugins(lines)
        except Exception:  # health: allow BLE001 -- the notice is best effort; the log line above already says it
            logger.debug("core patches: the missing-plugin notice failed", exc_info=True)
        return gap
    except Exception:  # health: allow BLE001 -- a startup check never breaks startup
        logger.debug("core patches: the missing-plugin check failed", exc_info=True)
        return {}
