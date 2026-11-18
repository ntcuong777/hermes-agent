"""Stage C: the dead lane modules are gone from core, nothing imports them, and only the seam and the marker remain (spec 08 stage C)."""

from pathlib import Path

import gateway

ROOT = Path(gateway.__file__).resolve().parent.parent


def test_the_dead_lane_modules_and_the_script_left_the_fork():
    for rel in ("gateway/session_lane.py", "gateway/session_lane_model.py", "scripts/session_model.py"):
        assert not (ROOT / rel).exists(), rel


SCANNED_DIRS = ("gateway", "hermes_cli", "tui_gateway", "tools", "scripts")


def _core_python_files():
    """Every core module the guard covers: the top-level *.py files and every *.py under the scanned packages (B13 F9)."""
    return [*sorted(ROOT.glob("*.py")),
            *(path for name in SCANNED_DIRS for path in sorted((ROOT / name).rglob("*.py")))]


def test_nothing_in_core_imports_the_old_lane_modules():
    files = _core_python_files()
    assert len(files) > 100 and ROOT / "hermes_state_local.py" in files  # the scan really covers the core
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "gateway.session_lane import" not in text and "gateway.session_lane_model" not in text, path.name
        assert "from gateway import session_lane\n" not in text, path.name


def test_the_fork_owned_lane_files_that_remain_are_the_seam_and_the_marker():
    lane = sorted(path.name for path in (ROOT / "gateway").glob("session_lane*.py"))
    assert lane == ["session_lane_features.py", "session_lane_seam.py"]
