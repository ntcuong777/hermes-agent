"""Cursor SDK usage accounting.

The SDK reports tokens only sometimes, and does not say whether ``inputTokens`` already includes cache
reads. Each report is checked against its own ``totalTokens`` to decide, and anything that does not add
up books only the figures that are certain. Missing usage is unknown, never zero; no cost is estimated.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.codex_runtime import _queue_token_counts

logger = logging.getLogger(__name__)

_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "totalTokens",
)


def canonical_counts(usage: Any) -> Optional[dict[str, int]]:
    """Hermes token buckets for one SDK usage report, or None when it is unusable."""
    if not isinstance(usage, dict):
        return None
    values = [usage.get(name) for name in _FIELDS]
    if not all(
        isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values
    ):
        return None
    inp, out, cache_read, cache_write, total = values
    reasoning = usage.get("reasoningTokens")
    reasoning = (
        reasoning
        if isinstance(reasoning, int)
        and not isinstance(reasoning, bool)
        and reasoning >= 0
        else 0
    )
    certain = {"completion_tokens": out, "output_tokens": out, "total_tokens": total}
    if inp + out + cache_read + cache_write == total:  # separate buckets
        uncached = inp
    elif inp + out == total:  # input already includes the cache tokens
        uncached = max(0, inp - cache_read - cache_write)
    else:  # cannot tell: book only what is certain
        return certain
    return {
        **certain,
        "input_tokens": uncached,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "reasoning_tokens": reasoning,
        "prompt_tokens": uncached + cache_read + cache_write,
    }


def record_usage(agent, usage: Any) -> dict[str, Any]:
    """Book one runtime call. Returns the usage fields for the turn result ({} when unknown)."""
    agent.session_api_calls = getattr(agent, "session_api_calls", 0) + 1
    counts = canonical_counts(usage)

    def billing(**extra: Any) -> dict[str, Any]:
        return dict(
            model=agent.model,
            billing_provider=agent.provider,
            billing_base_url=agent.base_url,
            api_call_count=1,
            **extra,
        )

    if counts is None:
        # No usable usage at all (tool-less turns report none): the compressor has no real
        # reading. The generic loop handles this in turn_usage; this runtime bypasses it, so
        # say it here (the codex app-server precedent, agent/codex_runtime.py) or preflight
        # compression defers on the rough estimate forever and a cursor_sdk session never
        # auto-compacts. Provider overflow cannot rescue it either: worker failures reach
        # Hermes as fixed codes, not API errors.
        _note_usage_less(agent)
        _queue_token_counts(
            agent, "Cursor api-call persistence failed (session=%s): %s", counts=billing
        )
        return {}
    if "prompt_tokens" not in counts:
        # The report did not reconcile against its own total: only the certain figures book,
        # and none of them adjudicates pressure — tell the compressor the same way.
        _note_usage_less(agent)
    for key, value in counts.items():
        setattr(agent, f"session_{key}", getattr(agent, f"session_{key}", 0) + value)
    _update_compressor(agent, counts)
    buckets = {
        k: counts[k]
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        )
        if k in counts
    }
    _queue_token_counts(
        agent,
        "Cursor token persistence failed (session=%s, tokens=%d): %s",
        counts["total_tokens"],
        counts=lambda: billing(**buckets),
    )
    return {
        **counts,
        **(
            {"last_prompt_tokens": counts["prompt_tokens"]}
            if "prompt_tokens" in counts
            else {}
        ),
    }


def _note_usage_less(agent) -> None:
    """Tell the compressor this response cannot adjudicate context pressure."""
    compressor = getattr(agent, "context_compressor", None)
    if compressor is None:
        return
    if getattr(compressor, "awaiting_real_usage_after_compression", False):
        try:
            compressor.update_from_response({})
        except Exception:
            logger.debug("cursor usage latch clear failed", exc_info=True)
    if callable(getattr(compressor, "note_usage_less_response", None)):
        try:
            compressor.note_usage_less_response()
        except Exception:
            logger.debug("cursor usage-less note failed", exc_info=True)


def _update_compressor(agent, counts: dict[str, int]) -> None:
    compressor = getattr(agent, "context_compressor", None)
    if compressor is None or "prompt_tokens" not in counts:
        return
    try:
        compressor.update_from_response(counts)
    except Exception:
        logger.debug("cursor usage compressor update failed", exc_info=True)
