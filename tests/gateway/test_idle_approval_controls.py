"""Out-of-turn approval cards in the session's pending controls (tools/approval_idle.py).

A background subagent's ask arrives when no turn is running. Its card is not bound to a running claim:
it survives an idle snapshot, follows the session into the next turn (re-stamped and re-shown), answers
only at its own stamp, and leaves the generation fence of every ordinary prompt exactly as it was.
"""

import pytest
from gateway.session_events import SessionEvents
from gateway.session_pending_controls import PendingControls
from hermes_state_runtime import RuntimeStoreError
from tools import approval

KEY = "agent:main:local:dm:idle-controls"
SID = "owned-session"


class Recorder(SessionEvents):
    def __init__(self):
        super().__init__()
        self.published = []

    def publish(self, session_id, payload, event_type=None, **kw):
        self.published.append((event_type, dict(payload)))
        return True


@pytest.fixture
def controls(monkeypatch):
    live = {}  # request_id -> entry, standing in for tools.approval's queue
    monkeypatch.setattr("gateway.session_pending_controls.list_gateway_approvals", lambda route: list(live.values()))
    resolved = []

    def resolve(route, choice, request_id=None, **_):
        resolved.append((route, choice, request_id))
        return 1 if live.pop(request_id, None) is not None else 0

    monkeypatch.setattr("gateway.session_pending_controls.resolve_gateway_approval", resolve)
    ctl = PendingControls(Recorder())
    ctl.live, ctl.resolved = live, resolved
    return ctl


def _ask(ctl, request_id, *, idle, generation):
    ctl.live[request_id] = {"request_id": request_id}
    data = {"request_id": request_id, "command": "<terminal>", "description": "child wants a shell"}
    if idle:
        data["idle"] = True
    ctl.register(SID, KEY, generation, data)


def test_an_idle_card_survives_an_idle_snapshot_but_an_ordinary_one_does_not(controls):
    _ask(controls, "idle1", idle=True, generation=3)
    _ask(controls, "turn1", idle=False, generation=3)
    kept = controls.snapshot(SID, None)  # no running turn
    assert [p["prompt_id"] for p in kept] == ["idle1"]


def test_a_new_turn_restamps_and_reshows_the_idle_card(controls):
    _ask(controls, "idle1", idle=True, generation=3)
    controls.events.published.clear()
    (prompt,) = controls.snapshot(SID, 4)
    assert prompt["execution_generation"] == 4
    assert [(t, p["execution_generation"]) for t, p in controls.events.published] == [("approval.request", 4)]
    controls.events.published.clear()
    controls.snapshot(SID, 4)
    assert controls.events.published == []  # once per generation, not per read


def test_an_older_generation_never_restamps_the_card_backwards(controls):
    _ask(controls, "idle1", idle=True, generation=5)
    (prompt,) = controls.snapshot(SID, 4)
    assert prompt["execution_generation"] == 5


def test_an_idle_card_answers_only_at_its_own_stamp(controls):
    _ask(controls, "idle1", idle=True, generation=3)
    with pytest.raises(RuntimeStoreError) as refused:
        controls.respond(SID, 2, "idle1", {"choice": "once"})
    assert refused.value.reason == "stale_generation"
    assert controls.resolved == []
    assert controls.respond(SID, 3, "idle1", {"choice": "once"})["status"] == "resolved"
    assert controls.resolved == [(KEY, "once", "idle1")]
    assert controls.snapshot(SID, None) == ()


def test_an_answered_or_expired_idle_card_leaves_the_pending_set(controls):
    _ask(controls, "idle1", idle=True, generation=3)
    controls.live.clear()  # the child timed out and left the queue
    assert controls.snapshot(SID, None) == ()
    assert "idle1" not in controls.idle


def test_an_ordinary_prompt_on_a_stale_generation_is_still_dropped(controls):
    _ask(controls, "turn1", idle=False, generation=3)
    assert controls.respond(SID, 4, "turn1", {"choice": "once"})["status"] == "already_resolved"
    assert controls.resolved == []


def test_the_real_queue_round_trip(controls, monkeypatch):
    """Idle card + the real tools.approval queue entry it mirrors."""
    monkeypatch.undo()  # use the real list/resolve against a real queue entry
    from tools.approval_gateway_wait import _ApprovalEntry
    entry = _ApprovalEntry({"request_id": "r1", "command": "<t>", "description": "d", "idle": True})
    approval._gateway_queues.setdefault(KEY, []).append(entry)
    try:
        real = PendingControls(Recorder())
        real.register(SID, KEY, 1, {**entry.data, "request_id": "r1"})
        assert real.respond(SID, 1, "r1", {"choice": "once"})["status"] == "resolved"
        assert entry.event.is_set() and entry.result == "once"
    finally:
        approval._gateway_queues.pop(KEY, None)
