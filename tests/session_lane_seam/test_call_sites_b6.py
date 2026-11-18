"""Stage B6 call site: the lane model carry reaches core only through one seam line (spec 08 stage B6)."""

import ast
from pathlib import Path

import gateway

ROOT = Path(gateway.__file__).resolve().parent.parent


def test_hand_off_conversations_records_the_route_through_the_seam():
    source = (ROOT / "hermes_state_local.py").read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "hand_off_conversations")
    assert ast.get_source_segment(source, node).count(".record_handoff(conn, target,") == 1


def test_no_core_file_mentions_the_moved_lane_model_module_any_more():
    for path in [*sorted((ROOT / "gateway").glob("*.py")), ROOT / "hermes_state_local.py"]:
        text = path.read_text(encoding="utf-8")
        assert "session_lane_model" not in text and "CARRY_PREFIX" not in text, path.name


def test_the_lane_model_module_left_core():
    assert not (ROOT / "gateway" / "session_lane_model.py").exists()
