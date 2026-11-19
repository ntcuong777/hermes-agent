"""Seam for the structured MCP approval card.

``_describe`` is what approval-card replaces. ``describe_mcp`` is the fault
boundary: a raising describe returns None, and the card stays command text.
A payload that already has ``arguments_cut`` is still deny-only in core.
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


def describe_mcp(server_name: str, tool_name: str, args: Any) -> Optional[dict]:
    """The MCP dict for a card, or None. A fault is None, never a partial dict."""
    try:
        described = _describe(server_name, tool_name, args)
    except Exception:
        logger.warning("MCP approval context failed for %s.%s", server_name, tool_name, exc_info=True)
        return None
    return described if isinstance(described, dict) else None
