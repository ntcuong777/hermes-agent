"""Cursor picker-row metadata: context window / fast / max-mode tags in the /model model stage.

The sidecar (``cache/cursor_models_meta.json``) is written by the cursor profile at fetch time
(``test_cursor_provider.py`` covers that); these cover the display side. Rows with no metadata
(or a sidecar that names none of them) render as bare ids — display chrome must never hide
a selectable row.
"""

from __future__ import annotations

import json

from hermes_cli import cursor_model_meta as cmm


def _write_sidecar(tmp_path, models):
    (tmp_path / "cache").mkdir(exist_ok=True)
    (tmp_path / "cache" / "cursor_models_meta.json").write_text(json.dumps({"models": models}))


def test_rows_carry_context_fast_and_max_tags(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ids = ["grok-4.7", "grok-4.7-500k", "grok-4.7-256k-fast", "claude-sonnet-5-5-1m", "composer-2.5"]
    _write_sidecar(tmp_path, {
        "grok-4.7": {"context": "500k", "fast": False, "max": True},
        "grok-4.7-500k": {"context": "500k", "fast": False, "max": True},
        "grok-4.7-256k-fast": {"context": "256k", "fast": True, "max": False},
        "claude-sonnet-5-5-1m": {"context": "1m", "fast": False, "max": True},
        "composer-2.5": {"fast": False, "max": False},
    })
    rows = cmm.cursor_row_labels(ids)
    width = max(len(m) for m in ids)
    assert rows[0] == "grok-4.7".ljust(width) + "  500K · max"
    assert rows[1] == "grok-4.7-500k".ljust(width) + "  500K · max"
    assert rows[2] == "grok-4.7-256k-fast".ljust(width) + "  256K · fast"
    assert rows[3] == "claude-sonnet-5-5-1m".ljust(width) + "  1M · max"
    assert rows[4] == "composer-2.5"  # nothing to say: bare id


def test_a_model_the_sidecar_does_not_name_stays_bare(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sidecar(tmp_path, {"grok-4.7": {"context": "500k", "fast": False, "max": True}})
    assert cmm.cursor_row_labels(["grok-4.7", "unknown-model"]) == [
        "grok-4.7".ljust(len("unknown-model")) + "  500K · max", "unknown-model"]


def test_a_missing_or_empty_sidecar_degrades_to_bare_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ids = ["grok-4.7", "composer-2.5-fast"]
    assert cmm.cursor_row_labels(ids) == ids
    _write_sidecar(tmp_path, {})
    assert cmm.cursor_row_labels(ids) == ids


def test_annotated_labels_keep_the_id_as_their_prefix(tmp_path, monkeypatch):
    # The picker's fuzzy filter and the id column alignment both read the label's head; the id
    # must stay verbatim at the start of the row.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sidecar(tmp_path, {"grok-4.7-500k": {"context": "500k", "fast": False, "max": True}})
    row = cmm.cursor_row_labels(["grok-4.7-500k"])[0]
    assert row.split()[0] == "grok-4.7-500k"
