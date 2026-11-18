"""Stage B15: the P27 pair and the permission_denied diagnostics are gone from core (spec 08b stage B15)."""

from pathlib import Path

import gateway

ROOT = Path(gateway.__file__).resolve().parent


def _text(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_session_local_and_session_policy_have_no_host_overlay_any_more():
    for name in ("session_local.py", "session_policy.py"):
        text = _text(name)
        assert "local_session_config" not in text and "read_host_file" not in text, name


def test_stage_local_session_reads_the_profile_config_plainly():
    assert "config = load_profile_config()\n" in _text("session_local.py")


def test_session_controls_has_no_permission_denied_diagnostics_any_more():
    text = _text("session_controls.py")
    assert "_log_denied_once" not in text and "_raise_site" not in text and "_DENIED_LOGGED" not in text
    assert "def _log_refusal(profile, method, exc: AuthorityRefusal) -> None:" in text


def test_route_config_stays_in_core_because_core_reads_it():
    assert (ROOT / "route_config.py").exists()


def test_session_local_keeps_the_pills_seam_line_of_stage_b5():
    assert ".complete_info(authority, live, info, ref.session_id, policy)" in _text("session_local.py")
