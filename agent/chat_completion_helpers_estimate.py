"""Cheap payload/chunk size estimates for the API-call helpers (stale-call detectors and
stream diagnostic counters). Sibling of ``agent.chat_completion_helpers``."""

from __future__ import annotations

import contextlib
from typing import Any

_IMAGE_PART_TYPES = frozenset({"image_url", "input_image", "image"})


def _image_part_chars(part: dict[str, Any], image_cost: int) -> int:
    """Char-equivalent of one image content part: the per-image cost learned from provider usage
    (x4 chars/token), never the base64 payload length. A single native screenshot priced as text
    read as ~100K+ tokens and selected the giant-conversation watchdog tiers (#63871, #76411)."""
    text = part.get("text")
    return image_cost * 4 + (len(text) if isinstance(text, str) else 0)


def _payload_chars(value: Any, image_cost: int) -> int:
    """``len(str(value))`` with image content parts priced at ``image_cost`` tokens each."""
    if value is None:
        return 0
    if isinstance(value, dict):
        part_type = value.get("type")
        # JSON-Schema nodes may hold a sub-schema (``properties.type``) or a multi-type list
        # under the "type" key; only scalar content-part types can ever match (#104793).
        if isinstance(part_type, str) and part_type in _IMAGE_PART_TYPES and any(k in value for k in ("image_url", "image", "source", "file_id")):
            return _image_part_chars(value, image_cost)
        return sum(len(str(k)) + 6 + _payload_chars(v, image_cost) for k, v in value.items())
    if isinstance(value, list):
        return sum(_payload_chars(item, image_cost) for item in value) + 2 * len(value)
    return len(str(value))


def _estimate_chunk_bytes(chunk: Any) -> int:
    """Cheap per-chunk size estimate for the stream diagnostic counters: delta
    string lengths plus a framing floor (~3x cheaper than ``len(repr(chunk))``
    in the agent's hottest loop). Unknown shapes just keep the floor."""
    size = 40  # SSE/JSON framing floor per chunk

    def _add(obj, *attrs):
        nonlocal size
        for attr in attrs:
            v = getattr(obj, attr, None)
            if isinstance(v, str):
                size += len(v)

    with contextlib.suppress(Exception):
        choices = getattr(chunk, "choices", None)
        if choices:
            delta = getattr(choices[0], "delta", None)
            if delta is not None:
                _add(delta, "content", "reasoning_content", "reasoning")
                for tc in getattr(delta, "tool_calls", None) or ():
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        _add(fn, "arguments", "name")
        else:
            _add(getattr(chunk, "delta", None), "text", "partial_json")
    return size
