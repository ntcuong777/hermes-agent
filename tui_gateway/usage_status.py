"""Cost and subscription-quota fields for the TUI status bar.

``server._get_usage`` calls these. Both are read-outs that must never slow or break usage
reporting: cost reads two attributes the agent already keeps, and quota reads the cache that
``agent.account_usage_cache`` fills in a daemon thread (the bar never waits on the network).
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

# Window kinds the bar shows. ``model``-scoped windows (an Opus-only weekly cap) never say the
# login itself is near its limit, so the bar leaves them to ``/usage``.
_ACCOUNT_SCOPE = "account"


def bar_wants(cfg: Any, name: str) -> bool:
    """Whether ``display.status_bar.fields`` lists *name*. These read-outs are opt-in: an empty or
    missing list is the default bar, which has neither, so nothing is fetched for it."""
    with contextlib.suppress(Exception):
        fields = cfg["display"]["status_bar"]["fields"]
        return name in {str(f).strip().lower() for f in fields}
    return False


_CARRIED_ATTR = "_bar_cost_carried"
_LINEAGE_CAP = (
    200  # a real compression lineage ran ~180 deep; the cap is only a backstop
)


def _cost_of(row: dict) -> float:
    return float(row.get("estimated_cost_usd") or 0)


# Markers a fork writes into the child's ``model_config``. Mirrors
# ``hermes_state_common._non_continuation_child_sql``: a marker rejects the child only when it names
# *this* parent, because a compression continuation inherits ``model_config`` verbatim and so
# carries its branch's marker, which names another row.
_FORK_MARKERS = ("_branched_from", "_delegate_from", "_reset_from")


def _is_continuation(child: dict, parent: dict, parent_id: str) -> bool:
    """Whether *child* continues *parent* after a compression (same conversation, so same bill).
    Branches, delegate children and resets share ``parent_session_id`` but are other conversations."""
    config = child.get("model_config")
    if isinstance(config, str):
        with contextlib.suppress(ValueError):
            config = json.loads(config)
    if isinstance(config, dict) and any(
        str(config.get(marker) or "") == parent_id for marker in _FORK_MARKERS
    ):
        return False
    return parent.get("end_reason") == "compression" and child.get("source") != "tool"


def _lineage_cost(db: Any, session_id: str) -> tuple[float, str]:
    """Stored cost of the session and its compression ancestors, and the tip row's cost status.

    ``get_session`` drains queued token deltas first, so the total already holds what this
    process has spent so far."""
    row = db.get_session(session_id)
    if not row:
        return 0.0, ""
    total, status = _cost_of(row), str(row.get("cost_status") or "")
    seen = {session_id}
    for _ in range(_LINEAGE_CAP):
        parent_id = row.get("parent_session_id")
        if not parent_id or parent_id in seen:
            break
        parent = db.get_session(parent_id)
        if not parent or not _is_continuation(row, parent, parent_id):
            break
        seen.add(parent_id)
        total += _cost_of(parent)
        row = parent
    return total, status


def _carried(agent: Any, counter: float) -> tuple[float, str]:
    """Cost this session had before this process started it, read once per agent.

    The agent's counter starts at zero in every process, so a resumed session would restart at
    $0 on the bar. The stored lineage total minus what the counter already holds is what came
    from earlier runs. A failed read is not remembered: the next call tries again."""
    cached = getattr(agent, _CARRIED_ATTR, None)
    if isinstance(cached, tuple):
        return cached
    db, session_id = (
        getattr(agent, "_session_db", None),
        getattr(agent, "session_id", ""),
    )
    result: tuple[float, str] = (0.0, "")
    if db is not None and session_id:
        try:
            stored, status = _lineage_cost(db, session_id)
        except Exception:
            return result
        result = (max(0.0, stored - counter), status)
    with contextlib.suppress(Exception):
        setattr(agent, _CARRIED_ATTR, result)
    return result


def cost_fields(agent: Any) -> dict[str, Any]:
    """``cost_usd`` (session total, only when above zero) and ``cost_status`` (when known).

    The total is the cost carried over from earlier runs of this session plus this run's
    counter, so resuming a session keeps counting where it stopped. ``cost_status`` is the
    agent's own word, else the stored one: ``estimated`` (a lower bound: only successful
    main-agent calls count), ``actual``, or ``included`` (a subscription that bills nothing).
    """
    out: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        counter = float(getattr(agent, "session_estimated_cost_usd", 0) or 0)
        carried, stored_status = _carried(agent, counter)
        amount = carried + counter
        status = str(getattr(agent, "session_cost_status", "") or "")
        if status in ("", "unknown") and carried > 0:
            status = stored_status
        if amount > 0:
            out["cost_usd"] = round(amount, 4)
        if status and status != "unknown" and (amount > 0 or status == "included"):
            out["cost_status"] = status
    return out


def quota_fields(agent: Any) -> dict[str, Any]:
    """``quota``: the active provider's account-scope windows, ``[{label, used_percent}]``.

    Only the provider the agent talks to (the person asked for the active account, not every
    account; ``/quota`` lists those). Reads the cache and asks for a background refresh, which
    is throttled per provider; nothing is fetched inline. Omitted when there is no fresh data.
    """
    out: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        from agent.account_usage_cache import (
            cached_account_usage,
            refresh_account_usage_async,
            snapshot_is_stale,
        )

        provider = str(getattr(agent, "provider", "") or "").strip().lower()
        if not provider:
            return out
        refresh_account_usage_async([provider])
        base_url = getattr(agent, "base_url", None)
        snapshot = cached_account_usage(provider) or cached_account_usage(
            provider, base_url=base_url
        )
        if snapshot is None or snapshot_is_stale(snapshot):
            return out
        windows = [
            {"label": w.label, "used_percent": round(float(w.used_percent), 1)}
            for w in snapshot.windows
            if w.used_percent is not None
            and getattr(w, "scope", _ACCOUNT_SCOPE) == _ACCOUNT_SCOPE
        ]
        if windows:
            out["quota"] = windows
    return out
