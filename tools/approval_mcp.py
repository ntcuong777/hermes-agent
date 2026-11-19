"""The one deny-only rule for an MCP call whose arguments were cut.

A card that does not hold the whole call must not be approved. The predicate
is core so it holds when the card plugin is absent, skipped, or faulted.
"""

from __future__ import annotations

from typing import Any


def arguments_cut(mcp: Any) -> bool:
    """True when the structured context says the arguments were cut."""
    return isinstance(mcp, dict) and bool(mcp.get("arguments_cut"))
