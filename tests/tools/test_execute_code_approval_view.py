"""A long execute_code script must be readable in full before it is approved (P21).

The TUI approval panel shows the first 10 wrapped lines of ``execute_code <<'PY' ... PY`` and then says the
rest is "above". It never was: the tool row above is the one-line preview ("exec: <first line>"). So the
person approved a script they could not read. A script longer than the panel is now saved, redacted, under
``<hermes home>/approvals/`` and the first line of the prompt names that file, so it is always visible.
"""

import os
import stat

import pytest

from tools import approval as A


@pytest.fixture
def home(tmp_path, monkeypatch):
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def fake_redactor(monkeypatch):
    import agent.redact as redact

    monkeypatch.setattr(
        redact,
        "redact_sensitive_text",
        lambda text, force=False: str(text).replace("TOPSECRET", "[REDACTED]"),
    )


def script(lines: int) -> str:
    return "\n".join(f"print({i})" for i in range(lines))


def test_a_short_script_is_shown_as_before_and_leaves_no_file(home):
    command = A._execute_code_command(script(5))
    assert command == f"execute_code <<'PY'\n{script(5)}\nPY"
    assert not (home / "approvals").exists()


def test_a_long_script_is_saved_and_its_path_is_the_first_line(home):
    code = script(40)
    command = A._execute_code_command(code)
    first = command.splitlines()[0]
    saved = home / "approvals"
    [file] = list(saved.iterdir())
    assert (
        first.startswith("execute_code <<'PY'")
        and str(file) in first
        and "40 lines" in first
    )
    assert file.read_text() == code  # the whole script, every line
    assert (
        command.endswith("\nPY") and "print(39)" in command
    )  # the panel text still carries the script


def test_the_saved_copy_is_redacted_like_the_panel(home):
    code = script(20) + "\nkey = 'TOPSECRET'"
    A._execute_code_command(code)
    [file] = list((home / "approvals").iterdir())
    assert "TOPSECRET" not in file.read_text() and "[REDACTED]" in file.read_text()


def test_the_directory_and_file_are_private(home):
    A._execute_code_command(script(30))
    root = home / "approvals"
    [file] = list(root.iterdir())
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(file.stat().st_mode) == 0o600


def test_the_same_script_is_the_same_file(home):
    A._execute_code_command(script(30))
    A._execute_code_command(script(30))
    assert len(list((home / "approvals").iterdir())) == 1
    A._execute_code_command(script(31))
    assert len(list((home / "approvals").iterdir())) == 2


def test_only_the_newest_files_are_kept(home, monkeypatch):
    monkeypatch.setattr(A, "_EXECUTE_CODE_VIEW_KEEP", 3)
    for n in range(20, 28):
        A._execute_code_command(script(n))
        newest = max((home / "approvals").iterdir(), key=lambda p: p.stat().st_mtime_ns)
        os.utime(newest, ns=(n * 10**9, n * 10**9))  # make the order unambiguous
    names = sorted(
        p.read_text().count("\n") + 1 for p in (home / "approvals").iterdir()
    )
    assert names == [25, 26, 27]


def test_an_unwritable_directory_never_blocks_the_approval(home, monkeypatch):
    (home / "approvals").write_text("a file where the directory should be")
    command = A._execute_code_command(script(30))
    assert command == f"execute_code <<'PY'\n{script(30)}\nPY"


def test_a_redactor_failure_never_blocks_the_approval_and_writes_nothing(
    home, monkeypatch
):
    import agent.redact as redact

    def boom(text, force=False):
        raise RuntimeError("redactor down")

    monkeypatch.setattr(redact, "redact_sensitive_text", boom)
    command = A._execute_code_command(script(30))
    assert command.startswith("execute_code <<'PY'\n")  # unchanged first line
    assert not (home / "approvals").exists() or not list((home / "approvals").iterdir())


def test_the_gate_asks_with_the_path_in_the_first_line(home, monkeypatch):
    asked = {}

    def capture(spec, *, command, **kw):
        asked["command"] = command
        return {"approved": True}

    monkeypatch.setattr(A, "_presence", lambda *a, **k: (None, False, True, False))
    monkeypatch.setattr(A, "_unattended_contexts", lambda: ())
    monkeypatch.setattr(A, "_yolo_active", lambda: False)
    monkeypatch.setattr(A.approval_context, "_get_approval_mode", lambda: "manual")
    monkeypatch.setattr(A, "is_approved", lambda *a, **k: False)
    monkeypatch.setattr(A, "_human_decision", capture)
    A.check_execute_code_guard(script(40), "local")
    assert "full script:" in asked["command"].splitlines()[0]
    assert str(home / "approvals") in asked["command"].splitlines()[0]


def test_an_already_approved_script_does_not_write_a_file(home, monkeypatch):
    monkeypatch.setattr(A, "_presence", lambda *a, **k: (None, False, True, False))
    monkeypatch.setattr(A, "_unattended_contexts", lambda: ())
    monkeypatch.setattr(A, "_yolo_active", lambda: False)
    monkeypatch.setattr(A.approval_context, "_get_approval_mode", lambda: "manual")
    monkeypatch.setattr(A, "is_approved", lambda *a, **k: True)
    A.check_execute_code_guard(script(40), "local")
    assert not (home / "approvals").exists()
