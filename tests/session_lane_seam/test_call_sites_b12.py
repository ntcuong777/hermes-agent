"""Stage B12: the settle-on-every-viewer code is gone from `PendingControls` (spec 08b stage B12); the plugin wraps `register`."""

import ast
from pathlib import Path

import gateway

SOURCE = (Path(gateway.__file__).resolve().parent / "session_pending_controls.py").read_text(encoding="utf-8")


def test_pending_controls_has_no_settle_code_left():
    assert "_settle_when_answered" not in SOURCE and "def withdraw" not in SOURCE and "observe_gateway_settle" not in SOURCE


def test_the_idle_approval_code_of_the_same_class_is_untouched():
    cls = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "PendingControls")
    methods = {m.name for m in cls.body if isinstance(m, ast.FunctionDef)}
    assert {"register", "register_clarify", "snapshot", "respond"} <= methods
    assert "self.idle = set()" in SOURCE and "data.get('idle') is True" in SOURCE
