"""A compression must not park the connection it arrives on (B2).

``handle_ws`` read one frame at a time and awaited each handler before the next. A ``/compress`` summarises the whole
transcript through a model and can take minutes, so every request queued behind it waited too: the model picker's
``model.options`` ("retrieving model..."), completions and every slash command, while the TUI looked stuck. The session
authority was built so a compress does not park other admissions; the connection loop undid it.

The fake authority connection answers instantly except for ``session.mutate`` on ``compress``, which waits for the
test to release it. Requests other than a compress keep strict arrival order.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from gateway import session_controls
from tui_gateway import server
from tui_gateway import ws as ws_mod

pytestmark = pytest.mark.asyncio


def _frame(request_id, method, **params):
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    )


def _mutate(request_id, operation):
    return _frame(
        request_id, "session.mutate", session_id="s", operation=operation, payload={}
    )


class _Harness:
    """`handle_ws` on a fake socket whose authority connection holds a compress (or `slow_op`) until released."""

    def __init__(self, monkeypatch, slow_op="compress"):
        self.release = asyncio.Event()
        self.replies: list = []
        self.dispatched: list = []
        self._frames: asyncio.Queue = asyncio.Queue()
        self._reply_seen = {request_id: asyncio.Event() for request_id in range(1, 6)}
        harness = self

        class FakeConnection:
            def __init__(self, authority, transport, identity, operator=False):
                self.actor = type(
                    "Actor", (), {"capabilities": frozenset(), "profile_id": "p"}
                )()

            async def dispatch(self, req):
                harness.dispatched.append(req["id"])
                params = req.get("params") or {}
                if (
                    req["method"] == "session.mutate"
                    and params.get("operation") == slow_op
                ):
                    await harness.release.wait()
                return {"jsonrpc": "2.0", "id": req["id"], "result": {"ok": True}}

            async def close(self):
                pass

        class FakeWS:
            scope = {"hermes.session_authority": object()}

            async def accept(self, **_kw):
                pass

            async def send_text(self, line):
                for part in line.splitlines():
                    if part.strip():
                        frame = json.loads(part)
                        if "id" in frame and "result" in frame:
                            harness.replies.append(frame["id"])
                            harness._reply_seen[frame["id"]].set()

            async def receive_text(self):
                item = await harness._frames.get()
                if item is None:
                    raise ws_mod._WebSocketDisconnect()
                return item

            async def close(self, **_kw):
                pass

        monkeypatch.setattr(session_controls, "AuthorityConnection", FakeConnection)
        monkeypatch.setattr(server, "_WS_ORPHAN_REAP_GRACE_S", 0)
        self._ws = FakeWS()
        self.task = None

    def start(self):
        self.task = asyncio.create_task(
            ws_mod.handle_ws(self._ws, auth_identity={"user_id": "u"})
        )

    async def send(self, frame):
        await self._frames.put(frame)

    async def disconnect(self):
        await self._frames.put(None)

    async def replied(self, request_id, timeout=2.0):
        await asyncio.wait_for(self._reply_seen[request_id].wait(), timeout)

    async def finish(self):
        self.release.set()
        await self.disconnect()
        await asyncio.wait_for(self.task, 5)


async def test_a_request_sent_while_a_compress_runs_is_answered_at_once(monkeypatch):
    h = _Harness(monkeypatch)
    h.start()
    try:
        await h.send(_mutate(1, "compress"))
        await h.send(_frame(2, "model.options", session_id="s"))
        await h.replied(
            2
        )  # red before the fix: parked behind the compress, so this times out
        assert h.replies == [2], "the compress is still running and has not replied"
    finally:
        await h.finish()
    assert h.replies == [2, 1], "the compress replies when it is done"


async def test_many_requests_during_a_compress_all_get_through(monkeypatch):
    h = _Harness(monkeypatch)
    h.start()
    try:
        await h.send(_mutate(1, "compress"))
        for request_id in (2, 3, 4):
            await h.send(_frame(request_id, "complete.path", session_id="s", word="@"))
        await h.replied(4)
        assert h.replies == [2, 3, 4]
    finally:
        await h.finish()


async def test_other_mutations_stay_in_arrival_order(monkeypatch):
    # Only a compress runs apart. A slow model switch still holds the requests behind it, as before.
    h = _Harness(monkeypatch, slow_op="model")
    h.start()
    try:
        await h.send(_mutate(1, "model"))
        await h.send(_frame(2, "model.options", session_id="s"))
        with pytest.raises(asyncio.TimeoutError):
            await h.replied(2, timeout=0.3)
    finally:
        await h.finish()
    assert h.replies == [1, 2]


async def test_ordinary_requests_reply_in_arrival_order(monkeypatch):
    h = _Harness(monkeypatch)
    h.start()
    try:
        for request_id in (1, 2, 3):
            await h.send(_frame(request_id, "complete.path", session_id="s", word="@"))
        await h.replied(3)
    finally:
        await h.finish()
    assert h.replies == [1, 2, 3]


async def test_a_disconnect_still_waits_for_the_compress_in_flight(monkeypatch):
    # The teardown parks the transport's sessions: it must not start under a compress that is still summarising.
    h = _Harness(monkeypatch)
    h.start()
    await h.send(_mutate(1, "compress"))
    await h.disconnect()
    await asyncio.sleep(0.3)
    assert not h.task.done(), "teardown ran while the compress was still in flight"
    h.release.set()
    await asyncio.wait_for(h.task, 5)
    assert h.replies == [1]
