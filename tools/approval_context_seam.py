"""Seams for the structured MCP approval card.

Both are what the approval-card plugin replaces. Absent, the card has no structured MCP block
and nothing here changes a choice, which is upstream.

``_describe`` builds the card's ``mcp`` dict. ``describe_mcp`` is the fault boundary: a raising
describe returns None, and the card stays command text.

``mcp_deny_only`` says a card's ``mcp`` context can only be denied (its arguments were cut, so
nobody could read the whole call). Every surface asks it, so the one plugin function decides.
The plugin's ``_describe`` raises rather than build a cut context while this still answers
False, so a half-installed plugin falls back to command text, not to an approvable cut call.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Hook for approval-card. The plugin replaces _describe. This stub returns None,
# so a missing plugin produces no structured MCP block.
def _describe(server_name: str, tool_name: str, args: Any) -> Optional[dict]:
    """Seam. Absent, the card has no structured MCP block."""
    del server_name, tool_name, args
    return None


def mcp_deny_only(mcp: Any) -> bool:
    """Seam. True when only Deny may be offered for this card context. Absent, False: upstream has no context."""
    del mcp
    return False


def describe_mcp(server_name: str, tool_name: str, args: Any) -> Optional[dict]:
    """The MCP dict for a card, or None. A fault is None, never a partial dict."""
    try:
        described = _describe(server_name, tool_name, args)
    except Exception:
        logger.warning("MCP approval context failed for %s.%s", server_name, tool_name, exc_info=True)
        return None
    return described if isinstance(described, dict) else None
