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


def test_a_prompt_registered_without_a_waiting_entry_is_left_to_the_next_read():
    """A synthetic registration (no wait behind it) has nothing to settle it: it stays pending."""
    events = _Events()
    controls = PendingControls(events)
    controls.register('sess-1', 'route-4', 3, {'request_id': 'solo-1', 'command': 'x', 'description': 'y'})
    assert _types(events) == [('approval.request', 'solo-1')]
    assert 'solo-1' in controls.pending
