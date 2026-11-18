"""Stage B11: the /cwd listing and the Discord /cwd slash left core; the call sites that remain are the seam's (spec 08b 9.2)."""

from pathlib import Path

import gateway

ROOT = Path(gateway.__file__).resolve().parent.parent


def _text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_list_sessions_has_the_two_seam_calls_and_no_cwd_code():
    text = _text("gateway/session_controls.py")
    assert text.count('session_lane_seam").session_lane_seam.list_params(params)') == 1
    assert text.count('session_lane_seam").session_lane_seam.list_row(row, workspace)') == 1
    assert "workspace_for_dir" not in text and "workspace_key(row)" not in text
    assert "'cwd', 'query'" not in text


def test_session_list_has_one_rows_seam_call_and_no_cwd_code():
    text = _text("tui_gateway/methods_session.py")
    assert text.count('session_lane_seam").session_lane_seam.list_rows(') == 1
    assert "rows_in_workspace" not in text and "workspace_for_dir" not in text


def test_no_core_file_keeps_the_cwd_listing_or_the_discord_cwd_slash():
    assert "workspace_for_dir" not in _text("hermes_state_sessions.py")
    assert "rows_in_workspace" not in _text("hermes_state_sessions.py")
    adapter = _text("plugins/platforms/discord/adapter.py")
    for name in ("_register_cwd_slash", "_build_cwd_choices", "_TEMPLATE_NONE_REGISTRARS", "workspace_candidates"):
        assert name not in adapter, name


def test_the_candidates_module_left_core():
    assert not (ROOT / "gateway" / "workspace_candidates.py").exists()


def test_the_marker_lists_workspace_slash():
    from gateway.session_lane_features import REMOVED_FROM_CORE

    assert "workspace-slash" in REMOVED_FROM_CORE
