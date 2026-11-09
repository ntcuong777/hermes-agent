"""Out-of-turn approval lane (tools/approval_idle.py): a background subagent's ask after its parent's turn.

The gateway only registers a session's approval notifier for the length of a turn, so a child that asks
later found nobody and was pushed to a chat. The lane is opt-in (``idle_ask``): every other approval keeps
its behaviour, and the turn-end sweep must not kill an ask that rides it.
"""

import threading
import time

import pytest
from tests.waiting import wait_until
from tools import approval, approval_idle

KEY = "agent:main:local:dm:idle-lane"


class Notifier:
    def __init__(self, viewers=True):
        self.viewers, self.seen = viewers, []

    def available(self):
        return self.viewers

    def __call__(self, data):
        self.seen.append(data)


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    approval.clear_session(KEY)
    yield
    approval_idle.unregister_idle_notify(KEY)
    approval.unregister_gateway_notify(KEY)
    approval.clear_session(KEY)


def _ask(box, *, idle, reason="child wants a shell"):
    from tools.approval_context import reset_current_session_key, set_current_session_key

    def run():
        token = set_current_session_key(KEY)
        try:
            if idle:
                with approval_idle.idle_ask():
                    box["result"] = approval.request_tool_approval("terminal", reason)
            else:
                box["result"] = approval.request_tool_approval("terminal", reason)
        finally:
            reset_current_session_key(token)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _pending():
    deadline = time.time() + 5
    while time.time() < deadline:
        if found := approval.list_gateway_approvals(KEY):
            return found
        time.sleep(0.005)
    return []


def test_an_idle_ask_reaches_the_idle_notifier_and_is_answerable():
    notifier = Notifier()
    approval_idle.register_idle_notify(KEY, notifier)
    box = {}
    thread = _ask(box, idle=True)
    assert _pending(), "the ask never reached the queue"
    wait_until(lambda: notifier.seen, what="idle notifier called")
    assert notifier.seen[0]["idle"] is True
    assert approval.resolve_gateway_approval(KEY, "once") == 1
    thread.join(timeout=5)
    assert box["result"]["approved"] is True


def test_the_lane_is_not_used_without_the_opt_in():
    notifier = Notifier()
    approval_idle.register_idle_notify(KEY, notifier)
    box = {}
    thread = _ask(box, idle=False)
    thread.join(timeout=5)
    assert notifier.seen == []
    assert not approval.list_gateway_approvals(KEY)


def test_the_lane_is_not_used_when_no_viewer_could_see_it():
    approval_idle.register_idle_notify(KEY, Notifier(viewers=False))
    assert approval_idle.idle_notify_for(KEY) is None


def test_a_child_ask_under_a_live_turn_rides_the_turn_notifier_but_survives_the_sweep():
    """The race the lane exists for, mid-turn: a child asks while its parent's turn is live, and
    the turn ends before anyone answers. Delivery stays on the live turn notifier, the entry is
    flagged idle, and the sweep leaves it waiting instead of withdrawing it (a later phone
    answer must still land, not wake the child with a BLOCKED nobody issued)."""
    lane, turn = Notifier(), Notifier()
    approval_idle.register_idle_notify(KEY, lane)
    approval.register_gateway_notify(KEY, turn)
    box = {}
    thread = _ask(box, idle=True)
    assert _pending()
    wait_until(lambda: turn.seen, what="turn notifier called")
    assert lane.seen == []
    assert turn.seen[0]["idle"] is True
    approval.unregister_gateway_notify(KEY)  # the parent's turn ended mid-wait
    assert approval.list_gateway_approvals(KEY), "the child's ask was killed by the sweep"
    approval.resolve_gateway_approval(KEY, "once")
    thread.join(timeout=5)
    assert box["result"]["approved"] is True


def test_the_turn_end_sweep_wakes_a_turn_ask_but_spares_an_idle_one():
    approval_idle.register_idle_notify(KEY, Notifier())
    idle_box, turn_box = {}, {}
    idle_thread = _ask(idle_box, idle=True)
    assert _pending()
    # A normal ask on the same key, raised while a turn notifier is registered.
    turn_notifier = Notifier()
    approval.register_gateway_notify(KEY, turn_notifier)
    turn_thread = _ask(turn_box, idle=False, reason="a different ask")
    wait_until(lambda: turn_notifier.seen, what="turn ask published")
    approval.unregister_gateway_notify(KEY)  # the turn ended
    turn_thread.join(timeout=5)
    assert turn_box["result"]["approved"] is False  # woken, fail closed
    assert idle_thread.is_alive(), "the idle ask was killed by the turn-end sweep"
    assert [p["request_id"] for p in approval.list_gateway_approvals(KEY)]
    approval.resolve_gateway_approval(KEY, "once")
    idle_thread.join(timeout=5)
    assert idle_box["result"]["approved"] is True
