"""A memory write that cannot fit is rejected to the model now, not after the person approves.

With ``memory.write_approval`` on, a single ``add`` was gated (inline prompt, or staged for
``/memory approve``) without ever checking the character limit; the limit only failed on
apply: "Approved 0 memory write(s). Failed: ... would exceed the limit by 12 chars". The
person approved a write that could not succeed and the model learned of it a turn later.
A batch was already dry-run when staged; the inline prompt path was not.
"""

from __future__ import annotations

import json

import pytest

from tools import write_approval as wa
from tools.memory_tool import MemoryStore, memory_tool
from tools.skill_provenance import reset_current_write_origin, set_current_write_origin


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    s = MemoryStore(memory_char_limit=100, user_char_limit=100)
    s.load_from_disk()
    s.add("memory", "x" * 90)

    return s


@pytest.fixture()
def prompts(monkeypatch):
    """Gate on; the inline prompt records every question and always approves."""
    asked: list[str] = []
    monkeypatch.setattr(wa, "write_approval_enabled", lambda subsystem: True)
    monkeypatch.setattr(
        wa,
        "_prompt_inline_memory_approval",
        lambda summary, detail: asked.append(summary) or True,
    )

    return asked


def _call(store, **kwargs):
    return json.loads(memory_tool(store=store, **kwargs))


def test_an_add_that_cannot_fit_is_rejected_before_the_person_is_asked(store, prompts):
    result = _call(store, action="add", content="y" * 20)

    assert result["success"] is False
    assert "chars: free at least" in result["error"]
    assert prompts == []
    assert wa.pending_count(wa.MEMORY) == 0


def test_a_background_add_that_cannot_fit_is_not_staged(store, prompts):
    token = set_current_write_origin("background_review")
    try:
        result = _call(store, action="add", content="y" * 20)
    finally:
        reset_current_write_origin(token)

    assert result["success"] is False and "staged" not in result
    assert wa.pending_count(wa.MEMORY) == 0


def test_a_batch_that_cannot_fit_is_rejected_before_the_person_is_asked(store, prompts):
    result = _call(store, operations=[{"action": "add", "content": "y" * 20}])

    assert result["success"] is False
    assert prompts == []


def test_an_add_that_fits_still_asks_and_applies(store, prompts):
    result = _call(store, action="add", content="z" * 5)

    assert result["success"] is True
    assert len(prompts) == 1
    assert "z" * 5 in store._entries_for("memory")


def test_a_batch_that_frees_room_still_asks_and_applies(store, prompts):
    result = _call(
        store,
        operations=[
            {"action": "remove", "old_text": "x" * 90},
            {"action": "add", "content": "y" * 60},
        ],
    )

    assert result["success"] is True
    assert len(prompts) == 1
