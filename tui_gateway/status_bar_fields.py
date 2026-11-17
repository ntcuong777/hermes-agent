"""Plugin-supplied, opt-in segments for the TUI status bar (``transform_status_bar_fields``).

The two places that build a ``session.usage`` frame (``tui_gateway.server._get_usage`` for the
embedded ticker, ``gateway.session_usage.usage_stats`` for the shared gateway) call
:func:`status_bar_fields` once. A plugin that registered the hook returns segments as plain data:
``{"id", "text", "tone"?, "min_cols"?}``. Core knows no segment by name; it validates the rows,
keeps only the ids the user listed in ``display.status_bar.fields`` (a segment is opt-in), and
carries them on ``Usage.status_fields`` for the TUI to place.

Nothing is dispatched for a bar that lists no field, so the default bar costs nothing, and a
plugin that raises is reported and skipped by ``invoke_hook_chained``: the frame never breaks.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any, Callable

HOOK = "transform_status_bar_fields"
TONES = frozenset({"muted", "good", "warn", "bad"})
MAX_FIELDS = 8
MAX_TEXT = 64  # a segment is a glance, not a sentence
MAX_MIN_COLS = 400
_ID = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")  # a plugin's text is drawn raw: no escape sequences


def wanted_fields(cfg: Any) -> tuple[str, ...]:
    """``display.status_bar.fields`` as lowercase names; empty for a missing, malformed or default bar."""
    with contextlib.suppress(Exception):
        return tuple(dict.fromkeys(str(f).strip().lower() for f in cfg["display"]["status_bar"]["fields"]))
    return ()


def clean_fields(raw: Any, wanted: tuple[str, ...]) -> list[dict[str, Any]]:
    """The valid, listed, de-duplicated rows of *raw*, in order. Anything else is dropped, not repaired."""
    if not isinstance(raw, (list, tuple)):
        return []
    rows: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        field_id, text = item.get("id"), item.get("text")
        if not (isinstance(field_id, str) and _ID.fullmatch(field_id) and field_id in wanted):
            continue
        text = _CONTROL.sub("", text).strip()[:MAX_TEXT] if isinstance(text, str) else ""
        if not text or field_id in rows:
            continue
        row: dict[str, Any] = {"id": field_id, "text": text}
        if item.get("tone") in TONES:
            row["tone"] = item["tone"]
        min_cols = item.get("min_cols")
        if isinstance(min_cols, int) and not isinstance(min_cols, bool) and 0 <= min_cols <= MAX_MIN_COLS:
            row["min_cols"] = min_cols
        rows[field_id] = row
    return list(rows.values())[:MAX_FIELDS]


def status_bar_fields(agent: Any, load_cfg: Callable[[], Any] | None = None) -> dict[str, Any]:
    """``{"status_fields": [...]}`` for the plugin segments the bar lists, else ``{}``. Never raises."""
    with contextlib.suppress(Exception):
        if load_cfg is None:
            from hermes_cli.config import load_config_readonly as load_cfg
        wanted = wanted_fields(load_cfg())
        if not wanted:
            return {}
        from hermes_cli.plugins import invoke_hook_chained

        rows = clean_fields(
            invoke_hook_chained(
                HOOK, "fields", accept=lambda value: isinstance(value, (list, tuple)),
                fields=[], session_id=str(getattr(agent, "session_id", "") or ""), agent=agent, wanted=wanted),
            wanted)
        return {"status_fields": rows} if rows else {}
    return {}
