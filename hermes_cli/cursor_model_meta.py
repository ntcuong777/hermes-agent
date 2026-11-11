"""Cursor picker-row metadata: context window / fast / max-mode tags in the ``/model`` model stage.

The cursor profile writes ``cache/cursor_models_meta.json`` on every SDK-catalog fetch (plugins/
model-providers/cursor). This module reads it cache-only — a picker render must never probe — and
builds the row labels: ``grok-4.7-500k  500K · max``. Rows the sidecar does not name (or a missing,
empty or unreadable sidecar) render as bare ids; display chrome must never hide a selectable row.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_META_SIDECAR = Path("cache") / "cursor_models_meta.json"


def _sidecar_path() -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()) / _META_SIDECAR


def load_model_meta() -> dict[str, dict[str, Any]]:
    """The sidecar's ``{id: {context, fast, max}}`` rows, or {} when absent/unreadable."""
    try:
        data = json.loads(_sidecar_path().read_text())
    except (OSError, ValueError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    return models if isinstance(models, dict) else {}


def cursor_row_labels(model_ids: list[str]) -> list[str]:
    """Picker rows for the cursor provider: the id (kept verbatim as the row's head — the fuzzy
    filter and selection resolve on it), then the tags the sidecar carries, right-aligned like the
    usage-window rows. No tags anywhere: the bare ids, unpadded."""
    ids = [str(model_id) for model_id in model_ids]
    meta = load_model_meta()
    rows: list[tuple[str, str]] = []
    for model_id in ids:
        entry = meta.get(model_id)
        tags: list[str] = []
        if isinstance(entry, dict):
            context = entry.get("context")
            if isinstance(context, str) and context:
                tags.append(context.upper())
            if entry.get("fast") is True:
                tags.append("fast")
            if entry.get("max") is True:
                tags.append("max")
        rows.append((model_id, " · ".join(tags)))
    if not any(tags for _model_id, tags in rows):
        return ids
    width = min(max(len(model_id) for model_id in ids), 48)
    return [f"{model_id.ljust(width)}  {tags}".rstrip() for model_id, tags in rows]
