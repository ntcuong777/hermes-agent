"""Session cost lineage: what a session (and the compression chain or branch it continues) has cost.

``tui_gateway.methods_session._persist_branch`` records ``lineage_cost`` of a parent in the branch row
(``FORK_COST_KEY``) so a branch keeps counting from the fork. The status bar's ``cost`` and ``quota``
segments are a plugin's (hook ``transform_status_bar_fields``, see ``tui_gateway/status_bar_fields.py``);
the quota plugin reads this lineage to add the cost carried over from earlier runs of a session.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

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
# What a branch's parent had cost when the branch was made (written by ``_persist_branch``).
FORK_COST_KEY = "_cost_at_fork_usd"
_FORK_DEPTH_CAP = (
    20  # branch-of-a-branch nesting; only a backstop against a parent loop
)


def _reader(db: Any):
    """``session_id -> row dict | None`` over either store shape: the legacy ``SessionDB`` (``get_session``) or the
    persistence handle an agent holds as ``_session_db`` (``sessions.get(...).as_session_row()``). A handle with
    neither raises here, loudly, rather than reading as a session that cost nothing."""
    getter = getattr(db, "get_session", None)
    if callable(getter):
        return getter

    def read(session_id: str):
        record = db.sessions.get(session_id)
        return record.as_session_row() if record is not None else None

    return read


def _config(row: dict) -> dict:
    config = row.get("model_config")
    if isinstance(config, str):
        with contextlib.suppress(ValueError):
            config = json.loads(config)
    return config if isinstance(config, dict) else {}


def _is_continuation(child: dict, parent: dict, parent_id: str) -> bool:
    """Whether *child* continues *parent* after a compression (same conversation, so same bill).
    Branches, delegate children and resets share ``parent_session_id`` but start a new row; a
    branch's bill is handled by ``_fork_cost``, the others are other conversations."""
    config = _config(child)
    if any(str(config.get(marker) or "") == parent_id for marker in _FORK_MARKERS):
        return False
    return parent.get("end_reason") == "compression" and child.get("source") != "tool"


def _fork_cost(db: Any, root: dict, depth: int) -> float:
    """What a branch copied from its parent: the cost recorded at the fork, else (rows from the
    classic CLI or older builds) the parent's cost now. 0 for anything that is not a branch."""
    parent_id = root.get("parent_session_id")
    config = _config(root)
    if not parent_id or str(config.get("_branched_from") or "") != parent_id:
        return 0.0
    recorded = config.get(FORK_COST_KEY)
    if (
        isinstance(recorded, (int, float))
        and not isinstance(recorded, bool)
        and recorded >= 0
    ):
        return float(recorded)
    if depth >= _FORK_DEPTH_CAP:
        return 0.0
    return lineage_cost(db, parent_id, _depth=depth + 1)[0]


def lineage_cost(db: Any, session_id: str, *, _depth: int = 0) -> tuple[float, str]:
    """Cost of the session, and the tip row's cost status.

    That is the stored cost of its row and of its compression ancestors (one conversation that
    rotated rows), plus, when the chain began as a branch, what the branch copied from its parent.
    Delegate children, resets and tool sessions are other conversations and add nothing.

    ``get_session`` drains queued token deltas first, so the total already holds what this
    process has spent so far."""
    get_session = _reader(db)
    row = get_session(session_id)
    if not row:
        return 0.0, ""
    total, status = _cost_of(row), str(row.get("cost_status") or "")
    seen = {session_id}
    for _ in range(_LINEAGE_CAP):
        parent_id = row.get("parent_session_id")
        if not parent_id or parent_id in seen:
            break
        parent = get_session(parent_id)
        if not parent or not _is_continuation(row, parent, parent_id):
            break
        seen.add(parent_id)
        total += _cost_of(parent)
        row = parent
    return total + _fork_cost(db, row, _depth), status


def fork_cost_config(persistence, parent_key: str) -> dict:
    """The ``model_config`` entry a branch records: what its parent had cost at the fork (``FORK_COST_KEY``), so the
    branch keeps counting from there. Empty when the parent cost nothing or cannot be read (the branch still forms)."""
    from types import SimpleNamespace

    def _row(session_id: str):
        record = persistence.sessions.get(session_id)
        return record.as_session_row() if record is not None else None

    with contextlib.suppress(Exception):
        if fork_cost := lineage_cost(SimpleNamespace(get_session=_row), parent_key)[0]:
            return {FORK_COST_KEY: round(fork_cost, 6)}
    return {}
