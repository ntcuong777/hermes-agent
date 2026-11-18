"""The fork edits that the core-fixes plugin now applies as core patches stay out of core (spec 03).

The plugin (nix-darwin hermes/plugins/core-fixes, cw_*.py) wraps these upstream functions through
the core-patch registry; .github/core-patch-pins.json pins each one's fingerprint. Re-adding an
edit here would make it apply twice (the plugin skips a fix it finds in core, so the edit would
silently win). If a test fails after an upstream merge, upstream now carries the fix: drop the
patch from the plugin instead of keeping both.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import subprocess
from pathlib import Path

import pytest

# agent/tool_executor.py is not byte-identical to upstream: spec 04 (hooks) keeps one fork line in
# _budget_for_agent. The test checks that restoring upstream's line makes the file upstream's blob.
TOOL_EXECUTOR_HOOK_LINE = (
    '        return __import__("tools.tool_result_limits").tool_result_limits.apply_budget_limits('
    "budget_for_context_window(int(ctx) if ctx else None), int(ctx) if ctx else None)"
    "  # fork: resolve_tool_result_limits hook"
)
TOOL_EXECUTOR_UPSTREAM_LINE = "        return budget_for_context_window(int(ctx) if ctx else None)"
# 2026-11-18 upstream sync to 0.22.5 (reason: upstream sync to 0.22.5); was 20f057249ce3c28af48f4da6298f62d500fa9f38.
TOOL_EXECUTOR_UPSTREAM_BLOB = "55cbaae263b4297a8b3676b546f35919155fcb5f"  # git rev-parse refs/heads/main:agent/tool_executor.py


def _module(name: str):
    return importlib.import_module(name)


def test_checkpoint_stale_lock_is_not_in_core():
    # 2026-11-18 upstream sync to 0.22.5: tools/checkpoint_manager.py moved to agent/checkpoints/manager.py.
    assert not hasattr(_module("agent.checkpoints.manager"), "_clear_stale_store_lock")


def test_tmux_passthrough_is_not_in_core():
    assert not hasattr(_module("hermes_cli.terminal_notify"), "tmux_passthrough")


def test_deferred_tool_hint_is_not_in_core():
    assert not hasattr(_module("agent.turn_tool_validation"), "deferred_tool_names_for_errors")
    params = inspect.signature(_module("agent.conversation_loop")._invalid_tool_name_error_content).parameters
    assert list(params) == ["name", "valid_tool_names"]


def test_cron_external_interpreter_is_not_in_core():
    source = inspect.getsource(inspect.unwrap(_module("cron.scheduler_script")._posix_cron_script_argv))
    assert "updateMechanism" not in source


def test_edit_diff_host_backend_is_not_in_core():
    assert not hasattr(_module("agent.display"), "_edits_land_on_this_host")


def test_patch_mode_targets_fallback_is_not_in_core():
    core = inspect.unwrap(_module("agent.tool_dispatch_helpers")._extract_file_mutation_targets)
    assert core("patch", {"mode": "patch", "path": "x.rs"}) == []


def test_sandbox_tool_names_is_not_in_core():
    assert not hasattr(_module("agent.tool_executor"), "sandbox_tool_names")


def test_tool_executor_is_upstream_except_the_spec_04_hook():
    data = Path(_module("agent.tool_executor").__file__).read_bytes()
    text = data.decode("utf-8")
    assert "sandbox_tool_names" not in text
    assert text.count(TOOL_EXECUTOR_HOOK_LINE) == 1
    restored = text.replace(TOOL_EXECUTOR_HOOK_LINE, TOOL_EXECUTOR_UPSTREAM_LINE).encode("utf-8")
    blob = hashlib.sha1(b"blob %d\x00" % len(restored) + restored).hexdigest()
    assert blob == TOOL_EXECUTOR_UPSTREAM_BLOB


# Upstream blob ids (git rev-parse <upstream>:<path>) of the files the core-fixes wrappers patch
# back: the reverted fork edits must leave these byte-identical to upstream.
# 2026-11-18 upstream sync to 0.22.5 (reason: upstream sync to 0.22.5): the four changed files
# and the checkpoint module (now agent/checkpoints/manager.py) carry the 0.22.5 blobs; the other
# three are unchanged upstream. The fork's merged files were checked byte-identical to upstream.
REVERTED_FILES_UPSTREAM_BLOBS = (
    ("agent/checkpoints/manager.py", "f39334cc679ae901f75b940b2303f2a544f4a2ff"),
    ("hermes_cli/terminal_notify.py", "db57eae7c8ad847616e814f0470cde984d09914a"),
    ("agent/turn_tool_round.py", "7f3b7b108152f3c92ce419642eb792b9eea03a5f"),
    ("agent/turn_tool_validation.py", "17644e898e52aba27bb63b6c2394b2992d74e3cd"),
    ("cron/scheduler_script.py", "82ca6447ae718fc96ec47b4e867906f070577a21"),
    ("agent/display.py", "0a129b056c2da0bc42a189dad1ce4e5e2505a3ce"),
    ("agent/tool_dispatch_helpers.py", "9d774b4e0ddc2c757a7217190ad8771fc4f7b199"),
    ("agent/agent_runtime_helpers.py", "9da502a2dc9c250cb38d47db0e5de688895c6beb"),
)


@pytest.mark.parametrize("path, upstream_blob", REVERTED_FILES_UPSTREAM_BLOBS)
def test_the_reverted_files_are_byte_identical_to_upstream(path, upstream_blob):
    repo_root = Path(__file__).resolve().parents[2]
    working_blob = subprocess.run(
        ["git", "hash-object", path], cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert working_blob == upstream_blob
