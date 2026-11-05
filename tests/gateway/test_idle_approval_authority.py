"""SessionAuthority's two idle-approval seams: registering an out-of-turn ask, and answering one.

Stubs stand in for the authority's store and sessions: only the idle branches are exercised here; the
running-turn generation fence keeps its own tests (test_session_authority*, test_local_viewer_events_turn).
"""

import asyncio
from types import SimpleNamespace

import pytest
from gateway.session_authority import SessionAuthority
from gateway.session_events import SessionEvents
from gateway.session_idle_approval import IdleApprovalNotifier
from gateway.session_pending_controls import PendingControls
from hermes_state_runtime import RuntimeStoreError

SID = "sess"


class Events(SessionEvents):
    def publish(self, *a, **k):
        return True


def _authority(*, viewers, running=False, generation=7, monkeypatch=None):
    live = SimpleNamespace(subscribers={"v": object()} if viewers else {}, event_stream=Events(), controls=None)
    live.controls = PendingControls(live.event_stream)
    handle = SimpleNamespace(execution_generation=generation, execution_state="running" if running else "idle")
    stub = SimpleNamespace(sessions={SID: live}, profile_id="p", _handle=lambda ref: handle)
    stub.check_approval_generation = lambda session_id, gen: SessionAuthority.check_approval_generation(stub, session_id, gen)
    stub.register_idle_approval = lambda *a, **k: SessionAuthority.register_idle_approval(stub, *a, **k)
    stub.has_viewer = lambda sid: SessionAuthority.has_viewer(stub, sid)
    stub.authorize = lambda *_: None
    return stub, live


def test_an_idle_ask_is_stamped_with_the_latest_generation_and_flagged_idle():
    stub, live = _authority(viewers=True)
    stub.register_idle_approval(SID, "route", {"request_id": "r1", "command": "<t>", "description": "d"})
    prompt = live.controls.pending["r1"][1]
    assert prompt["execution_generation"] == 7 and "r1" in live.controls.idle


def test_an_idle_ask_with_nobody_attached_is_refused_not_parked():
    stub, live = _authority(viewers=False)
    with pytest.raises(RuntimeStoreError) as refused:
        stub.register_idle_approval(SID, "route", {"request_id": "r1"})
    assert refused.value.reason == "no_viewer"
    assert live.controls.pending == {}


def test_the_notifier_reports_availability_from_the_attached_viewers():
    stub, _live = _authority(viewers=True)
    assert IdleApprovalNotifier(stub, SID, "route").available() is True
    stub, _live = _authority(viewers=False)
    assert IdleApprovalNotifier(stub, SID, "route").available() is False
    stub.sessions.clear()
    assert IdleApprovalNotifier(stub, SID, "route").available() is False


def test_an_idle_ask_is_answered_without_a_running_claim_but_an_ordinary_one_is_not(monkeypatch):
    actor = object()
    stub, live = _authority(viewers=True, running=False)
    live.subscribers = {"v": actor}
    stub.authorize = lambda *_: None
    resolved = []
    monkeypatch.setattr("gateway.session_pending_controls.list_gateway_approvals",
                        lambda route: [{"request_id": "r1"}, {"request_id": "o1"}])
    monkeypatch.setattr("gateway.session_pending_controls.resolve_gateway_approval",
                        lambda route, choice, request_id=None, **_: resolved.append(request_id) or 1)
    stub.register_idle_approval(SID, "route", {"request_id": "r1", "command": "<t>", "description": "d"})
    live.controls.register(SID, "route", 7, {"request_id": "o1", "command": "c", "description": "d"})
    ref = SimpleNamespace(session_id=SID)

    def respond(prompt_id):
        return asyncio.run(SessionAuthority.respond(stub, actor, ref, 7, prompt_id, {"choice": "once"}))

    assert respond("r1")["status"] == "resolved"  # idle: no running claim needed
    with pytest.raises(RuntimeStoreError) as refused:
        respond("o1")  # ordinary: the running-claim fence still applies
    assert refused.value.reason == "stale_generation"
    assert resolved == ["r1"]
