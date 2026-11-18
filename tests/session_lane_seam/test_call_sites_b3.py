"""Stage B3 call sites: the lane takeover reaches core only through four seam lines (spec 08 stage B3)."""

import ast
from pathlib import Path

import gateway

ROOT = Path(gateway.__file__).resolve().parent


def _source(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _function(name, qualname):
    tree = ast.parse(_source(name))
    parts = qualname.split(".")
    nodes = tree.body
    for part in parts:
        node = next(n for n in ast.walk(ast.Module(body=nodes, type_ignores=[]))
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part)
        nodes = node.body
    return ast.get_source_segment(_source(name), node)


def test_authorize_reports_each_check_to_the_seam():
    assert _function("session_authority.py", "SessionAuthority.authorize").count(".observe_check(ref.session_id, capability)") == 1


def test_dispatch_runs_both_handler_calls_through_the_guard():
    assert _function("session_controls.py", "AuthorityConnection.dispatch").count(".guard(") == 2


def test_mutate_asks_the_fail_closed_guard_before_the_admission_gate():
    segment = _function("session_mutations.py", "mutate_session")
    call = 'session_lane_seam").session_lane_seam.mutation_guard(authority, actor, ref, params)'
    assert segment.count(call) == 1
    assert segment.index(call) < segment.index("authority._require_admission_open()")


def test_rollback_asks_the_seam_for_a_policy():
    assert _function("session_tui_ops.py", "rollback").count(".rollback_policy(authority, ref, kind)") == 1


def test_no_core_file_imports_the_moved_lane_code_any_more():
    moved = {"note_authorize", "note_missing_policy", "viewer_policy_override", "call_with_rescue"}
    for path in sorted(ROOT.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("from gateway.session_lane import"):
                imported = {name.strip() for name in line.split(" import ", 1)[1].split(",")}
                assert not imported & moved, (path.name, line)


def test_the_gate_targets_are_imported_late_by_every_caller():
    # A registry wrap replaces the module attribute: a module-level `from ... import name` would keep the original.
    names = ("policy_for_source", "authorize_local_source", "mutate_session", "attach_file", "shell_exec")
    for path in sorted(ROOT.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("from gateway.") and " import " in line:
                assert not [name for name in names if name in line.split(" import ", 1)[1]], (path.name, line)
