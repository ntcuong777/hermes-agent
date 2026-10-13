"""A `patch` call with `mode: "patch"` but no V4A body still names the file in `path`.

A plugin that replaces `patch` (the hashline plugin) takes `path` and `edits`. A model that
remembers the stock tool can still send `mode: "patch"` with them. The core then looked for V4A
file headers in a `patch` body that is not there, found none, and reported no target: the path
scope of the parallel-write guard came out empty (a barrier) and the file-mutation summary named no
file. With no body, `path` is the target, as it is in replace mode. A real V4A call is unchanged.
"""

from __future__ import annotations

from agent.tool_dispatch_helpers import (
    _extract_file_mutation_targets,
    _extract_parallel_scope_paths,
)

V4A = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** Move File: b.py -> c.py\n*** End Patch"


def test_mode_patch_without_a_body_falls_back_to_path():
    args = {
        "mode": "patch",
        "path": "tests/x.rs",
        "edits": [{"op": "append", "lines": ["}"]}],
    }
    assert _extract_file_mutation_targets("patch", args) == ["tests/x.rs"]


def test_the_parallel_scope_names_that_file_instead_of_a_barrier(tmp_path):
    args = {"mode": "patch", "path": "x.rs", "edits": []}
    scope = _extract_parallel_scope_paths("patch", args, tmp_path)
    assert [p.name for p in scope] == ["x.rs"]


def test_a_real_v4a_body_still_wins_over_path():
    args = {"mode": "patch", "patch": V4A, "path": "ignored.py"}
    assert _extract_file_mutation_targets("patch", args) == ["src/a.py", "b.py", "c.py"]


def test_replace_mode_is_unchanged():
    assert _extract_file_mutation_targets(
        "patch", {"path": "a.py", "old_string": "x", "new_string": "y"}
    ) == ["a.py"]


def test_no_body_and_no_path_names_nothing():
    assert _extract_file_mutation_targets("patch", {"mode": "patch"}) == []


def test_an_empty_path_names_nothing():
    assert (
        _extract_file_mutation_targets("patch", {"mode": "patch", "path": "  "}) == []
    )
