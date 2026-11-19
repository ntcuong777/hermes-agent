"""Stage B5 call sites: the lane pills reach core only through two seam lines (spec 08 stage B5)."""

import ast
from pathlib import Path

import gateway

ROOT = Path(gateway.__file__).resolve().parent


def _function(name, qualname):
    source = (ROOT / name).read_text(encoding="utf-8")
    nodes = [n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == qualname]
    assert len(nodes) == 1, (name, qualname)
    return ast.get_source_segment(source, nodes[0])


def test_resume_asks_the_seam_for_the_attach_pills():
    assert _function("session_controls.py", "resume").count(".attach_info(self.authority, snapshot.handle.ref)") == 1


def test_local_session_info_hands_the_info_to_the_seam():
    assert _function("session_local.py", "local_session_info").count(".complete_info(authority, live, info, ref.session_id, policy)") == 1


def test_the_moved_lane_pill_code_is_gone_from_core():
    for name in ("session_local.py", "session_local_inventory.py", "session_controls.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "_complete_route" not in text and "lane_route_info" not in text and "LANE_ROUTE_FIELDS" not in text, name
