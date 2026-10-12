"""Cost and subscription-quota fields for the TUI status bar.

``server._get_usage`` calls these. Both are read-outs that must never slow or break usage
reporting: cost reads two attributes the agent already keeps, and quota reads the cache that
``agent.account_usage_cache`` fills in a daemon thread (the bar never waits on the network).
"""

from __future__ import annotations

import contextlib
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


def cost_fields(agent: Any) -> dict[str, Any]:
    """``cost_usd`` (session total, only when above zero) and ``cost_status`` (when known).

    ``cost_status`` is the agent's own word: ``estimated`` (a lower bound: only successful
    main-agent calls count), ``actual``, or ``included`` (a subscription that bills nothing).
    """
    out: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        amount = float(getattr(agent, "session_estimated_cost_usd", 0) or 0)
        status = str(getattr(agent, "session_cost_status", "") or "")
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
