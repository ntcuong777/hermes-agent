"""``resolve_reply_max_tokens``: plugins cap the per-call output tokens (fork-owned).

Called from the request builders of ``agent/chat_completion_helpers.py`` (Anthropic, Bedrock, Codex,
chat-completions, the Anthropic summary call) where upstream passes ``agent.max_tokens``: the
builder sends ``capped(agent, agent.max_tokens)`` instead. The truncation-retry ladder's one-shot
ephemeral cap is consumed by the builder before this and wins as upstream has it; the transports'
own later raises (Gemini thinking, the Anthropic thinking budget + 4096) still apply.

A callback gets ``base`` (the cap upstream would send, ``None`` when the model's own limit is
unknown), ``model``, ``provider``, ``session_id``, ``platform`` and returns a positive int or
``None``. The smallest valid cap wins; it replaces ``base`` when ``base`` is ``None`` or larger, so a
cap never raises the model's own. Fail-open: a callback that raises is skipped by the plugin
manager; no valid answer sends ``base`` unchanged.
"""

from __future__ import annotations

from typing import Any, Optional

HOOK = "resolve_reply_max_tokens"


def _caps(agent: Any, base: Optional[int]) -> list[int]:
    from hermes_cli.plugins import has_hook, invoke_hook

    if not has_hook(HOOK):
        return []
    results = invoke_hook(HOOK, base=base, model=str(getattr(agent, "model", "") or ""),
                          provider=str(getattr(agent, "provider", "") or ""),
                          session_id=str(getattr(agent, "session_id", "") or ""),
                          platform=str(getattr(agent, "platform", "") or ""))
    return [r for r in results if isinstance(r, int) and not isinstance(r, bool) and r > 0]


def capped(agent: Any, base: Optional[int]) -> Optional[int]:
    """``base`` lowered to the smallest plugin cap (or set to it when ``base`` is None)."""
    try:
        caps = _caps(agent, base)
    except Exception:  # health: allow BLE001 -- fail open: upstream's cap
        return base
    if not caps:
        return base
    cap = min(caps)
    return cap if base is None or base > cap else base
