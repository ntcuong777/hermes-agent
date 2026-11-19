"""Stage B14: the lane residue is gone from core (spec 08b stage B14); the plugin wraps and copies carry it."""

from pathlib import Path

import gateway

ROOT = Path(gateway.__file__).resolve().parent


def _text(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_publish_local_control_is_upstreams_again():
    text = _text("run_goals.py")
    assert "session_id=session_entry.session_id" not in text and "session_id: Optional[str] = None" not in text
    assert "await self._publish_local_control(source, only_if_present=True)" in text


def test_switch_session_keeps_only_the_terminal_cwd_across_a_repoint():
    text = _text("session.py")
    assert 'lane_reasoning' not in text and '{"terminal_cwd": old_entry.metadata["terminal_cwd"]}' in text


def test_apply_chat_pick_publishes_the_unstamped_session_info_again():
    text = _text("session_messaging_picks.py")
    assert "'authority_epoch': authority.epoch" not in text
    assert "live.event_stream.publish(owner.session_id, local_session_info(authority, owner), event_type='session.info')" in text
