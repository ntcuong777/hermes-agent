"""Stage C: the dead lane modules are gone from core, nothing imports them, and only the seam and the marker remain (spec 08 stage C)."""

from pathlib import Path

import gateway

ROOT = Path(gateway.__file__).resolve().parent.parent


def test_the_dead_lane_modules_and_the_script_left_the_fork():
    for rel in ("gateway/session_lane.py", "gateway/session_lane_model.py", "scripts/session_model.py"):
        assert not (ROOT / rel).exists(), rel


def test_nothing_in_core_imports_the_old_lane_modules():
    for path in [*sorted((ROOT / "gateway").rglob("*.py")), *sorted((ROOT / "hermes_cli").rglob("*.py")), ROOT / "hermes_state_local.py"]:
        text = path.read_text(encoding="utf-8")
        assert "gateway.session_lane import" not in text and "gateway.session_lane_model" not in text, path.name
        assert "from gateway import session_lane\n" not in text, path.name


def test_the_fork_owned_lane_files_that_remain_are_the_seam_and_the_marker():
    lane = sorted(path.name for path in (ROOT / "gateway").glob("session_lane*.py"))
    assert lane == ["session_lane_features.py", "session_lane_seam.py"]
