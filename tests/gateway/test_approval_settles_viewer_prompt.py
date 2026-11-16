"""An approval answered anywhere withdraws the card from every attached viewer.

A Discord button resolves ``tools.approval`` directly (it never passes through ``PendingControls.respond``),
so the TUI of a session mirrored onto the thread kept showing the prompt after it was approved there.
"""
import threading
from types import SimpleNamespace

import pytest

from gateway.session_pending_controls import PendingControls
from tools import approval
from tools.approval_gateway_wait import _await_gateway_decision


class _Events:
    def __init__(self):
        self.lock = threading.RLock()
        self.sent = []

    def publish(self, session_id, payload, event_type):
        self.sent.append((session_id, event_type, payload))


def _ask(controls, route, results, *, generation=3):
    """Run one gateway approval wait on a thread, registered with *controls* as a local viewer's prompt."""
    seen = {}

    def notify(data):
        seen['data'] = data
        controls.register('sess-1', route, generation, data)
        seen['registered'].set()

    seen['registered'] = threading.Event()

    def wait():
        results.append(_await_gateway_decision(route, notify, {
            'command': 'cat ~/notes', 'description': 'reads outside the workspace', 'pattern_key': 'k1'}))
    thread = threading.Thread(target=wait)
    thread.start()
    assert seen['registered'].wait(5)
    return thread, seen['data']['request_id']


@pytest.fixture(autouse=True)
def _clean_queues():
    yield
    with approval._lock:
        approval._gateway_queues.clear()


def _types(events):
    return [(kind, payload.get('prompt_id')) for _sid, kind, payload in events.sent]


def test_an_approval_resolved_outside_the_viewer_settles_its_card():
    events = _Events()
    controls = PendingControls(events)
    results = []
    thread, request_id = _ask(controls, 'route-1', results)
    assert _types(events) == [('approval.request', request_id)]

    # The platform card's tap: straight to tools.approval, never through controls.respond.
    assert approval.resolve_gateway_approval('route-1', 'once', request_id=request_id) == 1
    thread.join(5)

    assert results[0]['choice'] == 'once'
    assert _types(events) == [('approval.request', request_id), ('approval.settled', request_id)]
    assert controls.pending == {}


def test_a_denied_approval_settles_the_same_way():
    events = _Events()
    controls = PendingControls(events)
    results = []
    thread, request_id = _ask(controls, 'route-2', results)
    approval.resolve_gateway_approval('route-2', 'deny', request_id=request_id)
    thread.join(5)
    assert _types(events)[-1] == ('approval.settled', request_id)


def test_the_viewers_own_answer_settles_once():
    events = _Events()
    controls = PendingControls(events)
    results = []
    thread, request_id = _ask(controls, 'route-3', results)
    controls.respond('sess-1', 3, request_id, {'choice': 'once'})
    thread.join(5)
    settled = [kind for _sid, kind, _p in events.sent if kind == 'approval.settled']
    assert settled == ['approval.settled'], events.sent


def test_a_prompt_answered_before_its_card_registered_is_withdrawn_at_once():
    """The Discord send receipt is awaited before the viewer's card registers: a tap in that window pops the
    entry first, so there is nothing to watch and the card must not wait for a later read."""
    events = _Events()
    controls = PendingControls(events)
    controls.register('sess-1', 'route-4', 3, {'request_id': 'solo-1', 'command': 'x', 'description': 'y'})
    assert _types(events) == [('approval.request', 'solo-1'), ('approval.settled', 'solo-1')]
    assert controls.pending == {}


def test_an_ended_prompt_never_withdraws_the_sessions_other_live_prompts(monkeypatch):
    """An idle ask at generation 3 ends while the next turn's approval (generation 4) is still pending."""
    events = _Events()
    controls = PendingControls(events)
    live = {'idle-1', 'turn-2'}
    monkeypatch.setattr('gateway.session_pending_controls.list_gateway_approvals',
                        lambda route: [{'request_id': r} for r in live])
    controls.register('sess-1', 'route-5', 3, {'request_id': 'idle-1', 'command': 'a', 'description': 'd', 'idle': True})
    controls.register('sess-1', 'route-5', 4, {'request_id': 'turn-2', 'command': 'b', 'description': 'd'})
    live.discard('idle-1')  # the idle one is answered in Discord
    controls.withdraw('sess-1', 'idle-1')
    assert 'turn-2' in controls.pending and 'idle-1' not in controls.pending
    settled = [p['prompt_id'] for _s, k, p in events.sent if k == 'approval.settled']
    assert settled == ['idle-1']
