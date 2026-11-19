"""The structured MCP approval card context: its carrier, its fail-closed Deny-only rule, and its builder seam.

Three things live here and the order matters.

``card_context`` is the ONE carrier. ``_await_gateway_decision`` runs every ``approval_data["mcp"]`` through it
before the entry is queued, whoever produced it (the plugin builder, another plugin, a test): a non-dict is
dropped and every string in the dict, nested too, is redacted the way an approval command is. Every surface
reads the queued entry, so none of them redacts again and none can see a raw context.

``mcp_deny_only`` is the rule every surface asks (id-answer filter, canonical prompt, legacy TUI request,
typed ``/approve`` text, button card). It fails closed. A context whose ``arguments_cut`` mark is set is
Deny-only even when the policy hook raises or answers False, because nobody could read the whole call. The
hook ``_deny_only`` is the approval-card plugin's extension point and can only add reasons; core's stub adds
none. Without the plugin no context exists, so nothing is denied: upstream behavior.

``_describe`` is what the plugin replaces to build the dict; ``describe_mcp`` is its fault boundary.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        from agent.redact import redact_approval_display
        return redact_approval_display(value, force=True)
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def card_context(mcp: Any) -> Optional[dict]:
    """The one carrier: a redacted copy of a card's ``mcp`` context, or None when it is not a dict."""
    return _clean(mcp) if isinstance(mcp, dict) else None


# Hook for approval-card. The plugin replaces _describe. This stub returns None,
# so a missing plugin produces no structured MCP block.
def _describe(server_name: str, tool_name: str, args: Any) -> Optional[dict]:
    """Seam. Absent, the card has no structured MCP block."""
    del server_name, tool_name, args
    return None


def _deny_only(mcp: dict) -> bool:
    """Seam. Policy hook: True to make this context Deny-only. Absent, False (it can only add reasons)."""
    del mcp
    return False


def mcp_deny_only(mcp: Any) -> bool:
    """True when only Deny may be offered for this card context. Fails closed on a cut call.

    False only for a non-dict, or a dict with no ``arguments_cut`` mark that the hook does not deny. A hook
    that raises is logged and leaves a marked context Deny-only.
    """
    if not isinstance(mcp, dict):
        return False
    cut = bool(mcp.get("arguments_cut"))
    try:
        return bool(_deny_only(mcp)) or cut
    except Exception:
        logger.warning("mcp deny-only hook failed; a cut context stays Deny-only", exc_info=True)
        return cut


def describe_mcp(server_name: str, tool_name: str, args: Any) -> Optional[dict]:
    """The MCP dict for a card, or None. A fault is None, never a partial dict."""
    try:
        described = _describe(server_name, tool_name, args)
    except Exception:
        logger.warning("MCP approval context failed for %s.%s", server_name, tool_name, exc_info=True)
        return None
    return described if isinstance(described, dict) else None
