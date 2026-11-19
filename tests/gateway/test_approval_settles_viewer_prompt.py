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


def test_without_the_plugin_an_approval_answered_elsewhere_is_not_withdrawn():
    """The settle-on-every-viewer code is the `lane-mirror` plugin's (stage B12): core alone leaves the card until a read (upstream)."""
    events = _Events()
    controls = PendingControls(events)
    results = []
    thread, request_id = _ask(controls, 'route-absent', results)
    assert approval.resolve_gateway_approval('route-absent', 'once', request_id=request_id) == 1
    thread.join(5)
    assert results[0]['choice'] == 'once'
    assert _types(events) == [('approval.request', request_id)]  # no `approval.settled`
    assert request_id in controls.pending
