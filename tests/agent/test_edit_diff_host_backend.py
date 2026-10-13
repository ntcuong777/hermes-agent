"""Inline edit diffs for a plugin backend that runs on this host (nono) and a patch tool that answers in text.

Two things kept the TUI from showing a diff after `patch`:

* `capture_local_edit_snapshot` returned nothing unless the terminal backend was exactly
  ``local``. A plugin backend that declares ``is_remote = False`` (nono: a sandbox around host
  bash) edits files on this host too, so the before-state is readable.
* `extract_edit_diff` took a plain-text result for a failure. A `patch` replacement (the
  hashline plugin) answers with its anchor echo, not JSON, and raises on failure; the registry
  turns a raise into ``{"error": ...}``. A text result is a success, and the snapshot decides
  whether anything changed.

A remote backend (``is_remote`` True) still gets no snapshot: a file of the same name on this host
is an unrelated file.
"""

from __future__ import annotations

import json

import pytest

from agent import display, terminal_env_registry
from agent.terminal_env_provider import TerminalEnvironmentProvider


def _register(monkeypatch, name: str, *, is_remote: bool) -> None:
    backend_name, remote = name, is_remote

    class _Provider(TerminalEnvironmentProvider):
        name = backend_name
        display_name = backend_name

        def is_available(self):
            return True

        def create_environment(self, **kwargs):
            raise AssertionError("a diff preview must not start the backend")

    _Provider.is_remote = remote
    monkeypatch.setitem(terminal_env_registry._providers, name, _Provider())


def _backend(monkeypatch, name: str) -> None:
    monkeypatch.setattr(
        "tools.file_tools_paths._terminal_env_type_for_task",
        lambda task_id="default": name,
    )


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("one\ntwo\nthree\n")
    return path


def test_a_host_plugin_backend_gets_a_snapshot(monkeypatch, target):
    _register(monkeypatch, "nono", is_remote=False)
    _backend(monkeypatch, "nono")
    snapshot = display.capture_local_edit_snapshot(
        "patch", {"path": str(target)}, task_id="t"
    )
    assert snapshot is not None and snapshot.paths == [target]


def test_a_remote_plugin_backend_gets_none(monkeypatch, target):
    _register(monkeypatch, "farbox", is_remote=True)
    _backend(monkeypatch, "farbox")
    assert (
        display.capture_local_edit_snapshot("patch", {"path": str(target)}, task_id="t")
        is None
    )


@pytest.mark.parametrize("name", ["docker", "ssh", "unregistered-backend"])
def test_other_backends_still_get_none(monkeypatch, target, name):
    _backend(monkeypatch, name)
    assert (
        display.capture_local_edit_snapshot("patch", {"path": str(target)}, task_id="t")
        is None
    )


def test_local_still_gets_a_snapshot(monkeypatch, target):
    _backend(monkeypatch, "local")
    assert (
        display.capture_local_edit_snapshot("patch", {"path": str(target)}, task_id="t")
        is not None
    )


def _edited(monkeypatch, target):
    _backend(monkeypatch, "local")
    snapshot = display.capture_local_edit_snapshot(
        "patch", {"path": str(target)}, task_id="t"
    )
    target.write_text("one\nTWO\nthree\n")
    return snapshot


def test_a_text_result_from_patch_shows_the_diff(monkeypatch, target):
    snapshot = _edited(monkeypatch, target)
    echo = "1#AB:one\n2#CD:TWO\n3#EF:three"
    diff = display.extract_edit_diff(
        "patch", echo, function_args={"path": str(target)}, snapshot=snapshot
    )
    assert diff is not None and "-two" in diff and "+TWO" in diff


def test_a_text_result_with_no_change_shows_nothing(monkeypatch, target):
    _backend(monkeypatch, "local")
    snapshot = display.capture_local_edit_snapshot(
        "patch", {"path": str(target)}, task_id="t"
    )
    assert display.extract_edit_diff("patch", "1#AB:one", snapshot=snapshot) is None


def test_an_error_result_shows_nothing(monkeypatch, target):
    snapshot = _edited(monkeypatch, target)
    failed = json.dumps({"error": "E_STALE_ANCHOR"})
    assert display.extract_edit_diff("patch", failed, snapshot=snapshot) is None


def test_write_file_still_needs_a_json_success(monkeypatch, target):
    snapshot = _edited(monkeypatch, target)
    assert (
        display.extract_edit_diff("write_file", "wrote it", snapshot=snapshot) is None
    )
