"""Dotted-literal-key matching for nested config writes/reads (sibling of ``hermes_cli.config``).

Dots in config key names are the norm, not the exception — model IDs (``grok-4.6``, ``glm-5.3``),
Matrix room IDs (``!room:chat.example.cc``), and versioned provider names all embed dots. Users
typing ``providers.myprov.models.grok-4.6.context_length`` do not know the escape syntax exists, so
when navigating an EXISTING mapping we prefer an existing literal key equal to the dot-join of the
next N path segments (longest match wins) over blindly splitting. See #84064 / #80006 / 91095 /
#91607 / #99124."""

from __future__ import annotations

from typing import Optional


def _greedy_literal_match(container: dict, parts: list) -> Optional[tuple[str, int]]:
    """Return ``(literal_key, n_consumed)`` for the longest dotted literal key present in
    *container*, or None. With no multi-segment literal this is the historic plain-split walk."""
    if not isinstance(container, dict) or not parts:
        return None
    return next(
        ((".".join(parts[:n]), n) for n in range(len(parts), 0, -1) if ".".join(parts[:n]) in container),
        None)


def _phantom_sibling(container: dict, part: str) -> Optional[str]:
    """Existing literal dotted key that creating an intermediate mapping ``part`` would shadow
    (``grok-4`` beside ``grok-4.5``) — the write would produce a phantom sibling the runtime never
    reads, so callers fail loudly instead.

    Called when a write is about to CREATE a new intermediate mapping named ``part``. See #84064.
    """
    if not isinstance(container, dict):
        return None
    prefix = part + "."
    return next((k for k in container if isinstance(k, str) and k.startswith(prefix)), None)
