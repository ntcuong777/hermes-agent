"""DM plaintext admin phrases rewritten into slash commands (sibling of ``gateway.platforms.base``).

``restart gateway`` reaching the LLM/tool path self-restarts from inside the running agent and
leaves the gateway stuck in ``draining`` waiting on that agent, so the exact-match phrases become
the ``/restart`` slash command instead."""

from __future__ import annotations

import contextlib
import re

from gateway.platforms.event import MessageEvent, MessageType

_PLAINTEXT_GATEWAY_RESTART_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:please\s+)?restart\s+(?:the\s+)?gateway[.!?\s]*$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?restart\s+(?:the\s+)?hermes\s+gateway[.!?\s]*$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?restart\s+hermes[.!?\s]*$", re.IGNORECASE))


def coerce_plaintext_gateway_command(event: MessageEvent) -> None:
    """Rewrite a tiny set of DM plaintext admin phrases (exact matches only) into slash commands so
    ``restart gateway`` never reaches the LLM/tool path (a self-restart from inside the running
    agent leaves the gateway stuck in ``draining`` waiting on that agent)."""
    with contextlib.suppress(Exception):
        if event is None or event.message_type != MessageType.TEXT:
            return
        text = (event.text or "").strip()
        if not text or text.startswith("/"):
            return
        if getattr(getattr(event, "source", None), "chat_type", None) != "dm":
            return
        if any(pattern.match(text) for pattern in _PLAINTEXT_GATEWAY_RESTART_PATTERNS):
            event.text = "/restart"
