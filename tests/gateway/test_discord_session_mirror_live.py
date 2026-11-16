"""The session mirror's live feed (`session_mirror_live`).

With `session_mirror_live` the thread opens as soon as the session is titled and binding waits for
it to settle; the thread then follows the session through its canonical event stream (a read-only viewer's subscription): local prompts are
echoed `[name] text`, a local lane's tool activity is one coalesced status message and its reply is
posted split at 2000 chars, while a bound lane — which streams into the thread itself — only gets
the echo. Thread-originated input is never echoed.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.session_events import SessionEvents

from plugins.platforms.discord.adapter import DiscordAdapter
from plugins.platforms.discord import adapter_session_mirror as mirror
from plugins.platforms.discord import adapter_session_mirror_live as live_feed


def _adapter(extra=None):
    base = {"session_mirror_channel": "555", "group_allow_admin_from": ["202"]}
    base.update(extra or {})
    return DiscordAdapter(PlatformConfig(enabled=True, token="fake-token", extra=base))


def _message(content):
    return SimpleNamespace(content=content, edit=AsyncMock())


def _thread(tid=999):
    sent = []

    async def send(text):
        msg = _message(text)
        sent.append(msg)
        return msg
    return SimpleNamespace(id=tid, name="[h] t", edit=AsyncMock(), send=AsyncMock(side_effect=send), sent=sent)


def _channel(thread):
    channel = MagicMock()
    channel.id = 555
    channel.name = "sessions"
    channel.guild.id = 1
    channel.threads = [thread]
    channel.create_thread = AsyncMock(return_value=thread)
    return channel


def _filtering_db(rows):
    """`list_sessions_rich` honouring `sources` + `local_created_sources` like the store does."""
    def list_rich(**kw):
        sources, surfaces = kw.get("sources") or [], kw.get("local_created_sources") or []
        return [r for r in rows if r["source"] in sources and r["source"] != "local"
                or (r["source"] == "local" and "local" in sources and r.get("created_source") in surfaces)]
    return SimpleNamespace(list_sessions_rich=list_rich, get_session=lambda sid: None, list_kwargs=None)


def _wire_roster(adapter, db, channel):
    adapter.gateway_runner = SimpleNamespace(
        session_store=SimpleNamespace(_db_for_key=lambda key: db),
        _session_key_for_source=lambda source: "agent:main:discord:dm:555",
        _profile_name_for_source=lambda source, adapter_profile=None: "main")
    adapter.handle_message = AsyncMock()
    adapter._client = MagicMock()
    adapter._client.get_channel = lambda cid: channel if str(cid) == "555" else None


def _row(sid, source, created=None, **extra):
    return {"id": sid, "title": f"title {sid}", "source": source, "created_source": created or source, **extra}


# ── live: thread before settle, binding after ─────────────────────────────────


@pytest.mark.asyncio
async def test_live_opens_the_thread_before_settle_and_binds_once_settled(monkeypatch):
    adapter = _adapter({"session_mirror_live": True})
    thread = _thread()
    channel = _channel(thread)
    _wire_roster(adapter, _filtering_db([_row("tui-1", "tui")]), channel)
    mirror._save_map(adapter, {})
    started = []
    monkeypatch.setattr(live_feed, "start_feed", lambda a, sid, db: started.append(sid))
    monkeypatch.setattr(live_feed, "ensure_feeds", lambda a, seen: None)

    async def busy(db, sid):
        raise RuntimeError("wait for the current turn to finish")
    monkeypatch.setattr(mirror, "check_settled", busy)
    await mirror.tick(adapter, mirror._load_map(adapter), post_new=True)

    channel.create_thread.assert_awaited_once()  # visible mid-first-turn
    assert "Mirrored session" in thread.sent[0].content
    assert started == ["tui-1"]
    adapter.handle_message.assert_not_called()  # the lane move still waits for the settle

    monkeypatch.setattr(mirror, "check_settled", AsyncMock())
    await mirror.tick(adapter, mirror._load_map(adapter), post_new=True)
    adapter.handle_message.assert_awaited_once()
    assert adapter.handle_message.await_args.args[0].text == "/resume tui-1 --all"

    await mirror.tick(adapter, mirror._load_map(adapter), post_new=True)  # row not flipped yet
    adapter.handle_message.assert_awaited_once()  # dispatched once, never re-sent
    channel.create_thread.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_off_keeps_thread_and_binding_behind_the_settle(monkeypatch):
    adapter = _adapter()
    thread = _thread()
    channel = _channel(thread)
    _wire_roster(adapter, _filtering_db([_row("tui-1", "tui")]), channel)
    mirror._save_map(adapter, {})
    started = []
    monkeypatch.setattr(live_feed, "start_feed", lambda a, sid, db: started.append(sid))

    async def busy(db, sid):
        raise RuntimeError("wait for the current turn to finish")
    monkeypatch.setattr(mirror, "check_settled", busy)
    await mirror.tick(adapter, mirror._load_map(adapter), post_new=True)

    channel.create_thread.assert_not_called()
    adapter.handle_message.assert_not_called()
    assert started == [] and getattr(adapter, "_mirror_live", None) is None


# ── live feed ─────────────────────────────────────────────────────────────────


class _Authority:
    def __init__(self, live, admissions):
        self.sessions = {"s1": live}
        self.executing = {}
        self.db = SimpleNamespace(admissions=admissions)

    def logical_owner(self, sid):
        return sid


def _viewer_row(text):
    return {"request_id": "sub-1", "payload": {"text": text}}


def _native_row(text):
    return {"request_id": "m-1", "payload": {"text": text, "native_text_v1": {"event": {"message_id": "m-1"}}}}


def _feed_adapter(monkeypatch, *, platform=Platform.LOCAL, admissions=None, extra=None):
    adapter = _adapter({"session_mirror_live": True, **(extra or {})})
    thread = _thread()
    channel = _channel(thread)
    adapter._client = MagicMock()
    adapter._client.get_channel = lambda cid: channel if str(cid) == "555" else None
    adapter._client.get_user = lambda uid: SimpleNamespace(display_name="Cuong")
    live = SimpleNamespace(source=SimpleNamespace(platform=platform), event_stream=SessionEvents())
    authority = _Authority(live, admissions if admissions is not None else {})
    adapter.gateway_runner = SimpleNamespace(session_authority=authority)
    monkeypatch.setattr("hermes_state_runtime.get_session_admission",
                        lambda db, admission_id: db.admissions.get(admission_id))
    mirror._save_map(adapter, {"s1": ("999", "Fix the gate")})
    return adapter, thread, authority, live


def _publish(live, admission_id, event_type, payload=None):
    live.event_stream.execution = {"admission_id": admission_id, "execution_generation": 1}
    live.event_stream.publish("s1", payload or {}, event_type=event_type)


async def _drain(adapter):
    """Let the observer's loop callbacks run, then wait for the worker to post everything."""
    await asyncio.sleep(0)
    await asyncio.wait_for(adapter._mirror_live.queue.join(), 5)


def _texts(thread):
    return [m.content for m in thread.sent]


@pytest.mark.asyncio
async def test_local_lane_turn_posts_echo_coalesced_status_and_split_reply(monkeypatch):
    monkeypatch.setattr(live_feed, "_STATUS_EDIT_SECONDS", 3600.0)  # every later edit is debounced
    adapter, thread, _authority, live = _feed_adapter(monkeypatch, admissions={"a1": _viewer_row("fix the gate")})
    live_feed.ensure_feeds(adapter, mirror._load_map(adapter))
    try:
        _publish(live, "a1", "message.start")
        for name in ("read_file", "terminal", "patch"):
            _publish(live, "a1", "tool.start", {"name": name, "context": "x", "tool_id": name})
        _publish(live, "a1", "tool.complete", {"name": "patch", "is_error": True, "tool_id": "patch"})
        reply = "word " * 700  # 3500 chars
        _publish(live, "a1", "message.complete", {"text": reply, "admission_id": "a1", "status": "complete"})
        await _drain(adapter)
    finally:
        live_feed.stop(adapter)

    texts = _texts(thread)
    assert texts[0] == "[Cuong] fix the gate"
    assert texts[1].startswith("⚙️ working… 1 tool call")  # one status message, not one per tool
    assert len(texts) == 2 + 2  # echo, status, the reply in two chunks
    assert all(len(t) <= 2000 for t in texts)
    assert "word" in texts[2] and "word" in texts[3]
    status = thread.sent[1]
    status.edit.assert_awaited_once_with(content="✓ 3 tool calls, 1 failed")  # the final in-place edit


@pytest.mark.asyncio
async def test_streamed_reply_without_final_text_posts_the_streamed_segment(monkeypatch):
    adapter, thread, _authority, live = _feed_adapter(monkeypatch, admissions={"a1": _viewer_row("hi")})
    live_feed.ensure_feeds(adapter, mirror._load_map(adapter))
    try:
        _publish(live, "a1", "message.start")
        _publish(live, "a1", "message.delta", {"text": "let me look"})
        _publish(live, "a1", "message.delta", {"text": None})
        _publish(live, "a1", "message.delta", {"text": "Hello "})
        _publish(live, "a1", "message.delta", {"text": "there"})
        _publish(live, "a1", "message.complete", {"text": None, "admission_id": "a1", "status": "complete"})
        await _drain(adapter)
    finally:
        live_feed.stop(adapter)
    assert _texts(thread) == ["[Cuong] hi", "Hello there"]


@pytest.mark.asyncio
async def test_thread_originated_input_is_never_echoed(monkeypatch):
    """A Discord message (or a forward, echoed by the forward itself) is a native admission: no
    echo. On a bound lane its reply streams natively, so the feed posts nothing at all."""
    adapter, thread, _authority, live = _feed_adapter(
        monkeypatch, platform=Platform.DISCORD, admissions={"a1": _native_row("typed in the thread")})
    live_feed.ensure_feeds(adapter, mirror._load_map(adapter))
    try:
        _publish(live, "a1", "message.start")
        _publish(live, "a1", "tool.start", {"name": "terminal", "tool_id": "t"})
        _publish(live, "a1", "message.complete", {"text": "done", "admission_id": "a1", "status": "complete"})
        await _drain(adapter)
    finally:
        live_feed.stop(adapter)
    assert _texts(thread) == []


@pytest.mark.asyncio
async def test_bound_lane_viewer_prompt_is_echoed_but_its_reply_is_left_to_the_lane(monkeypatch):
    adapter, thread, _authority, live = _feed_adapter(
        monkeypatch, platform=Platform.DISCORD, admissions={"a1": _viewer_row("from the TUI")})
    live_feed.ensure_feeds(adapter, mirror._load_map(adapter))
    try:
        _publish(live, "a1", "message.start")
        _publish(live, "a1", "tool.start", {"name": "terminal", "tool_id": "t"})
        _publish(live, "a1", "message.complete", {"text": "native reply", "admission_id": "a1", "status": "complete"})
        await _drain(adapter)
    finally:
        live_feed.stop(adapter)
    assert _texts(thread) == ["[Cuong] from the TUI"]


@pytest.mark.asyncio
async def test_recap_then_live_has_no_duplicates_and_rebinding_reattaches(monkeypatch):
    """A fresh thread recaps the stored conversation; the in-flight turn replays from the ring
    and its prompt/reply already in the recap are not posted twice. After the binding replaces
    the live session (a Discord lane), the feed follows the new stream and only echoes."""
    adapter, thread, authority, live = _feed_adapter(monkeypatch, admissions={
        "a1": _viewer_row("second prompt"), "a2": _viewer_row("from the TUI later"),
        "a3": _native_row("typed in the thread")})
    history = [{"role": "user", "content": "first prompt"}, {"role": "assistant", "content": "first reply"},
               {"role": "assistant", "content": "", "tool_calls": [{}]}, {"role": "tool", "content": "out"},
               {"role": "user", "content": "second prompt"}]
    db = SimpleNamespace(
        get_messages_as_conversation=lambda sid, **kw: list(history),
        list_sessions_rich=lambda **kw: [])
    # the in-flight turn started before the thread existed
    _publish(live, "a1", "message.start")
    authority.executing["s1"] = 1
    live_feed.start_feed(adapter, "s1", db)
    try:
        history.append({"role": "assistant", "content": "second reply"})  # persisted before its frame
        _publish(live, "a1", "message.complete", {"text": "second reply", "admission_id": "a1", "status": "complete"})
        await _drain(adapter)
        assert _texts(thread) == ["[Cuong] first prompt", "first reply", "[Cuong] second prompt", "second reply"]

        bound = SimpleNamespace(source=SimpleNamespace(platform=Platform.DISCORD), event_stream=SessionEvents())
        authority.sessions["s1"] = bound
        authority.executing.clear()
        _publish(bound, "a2", "message.start")  # before the tick re-attaches: replayed from the ring
        live_feed.ensure_feeds(adapter, mirror._load_map(adapter))
        _publish(bound, "a2", "message.complete", {"text": "native", "admission_id": "a2", "status": "complete"})
        _publish(bound, "a3", "message.start")
        _publish(bound, "a3", "message.complete", {"text": "native 2", "admission_id": "a3", "status": "complete"})
        await _drain(adapter)
        live_feed.ensure_feeds(adapter, mirror._load_map(adapter))  # idempotent
        await _drain(adapter)
    finally:
        live_feed.stop(adapter)
    assert _texts(thread)[4:] == ["[Cuong] from the TUI later"]
    assert live.event_stream.observers == set() and bound.event_stream.observers == set()


@pytest.mark.asyncio
async def test_local_forward_echoes_the_prompt_when_live():
    adapter, thread = _forward_setup({"session_mirror_live": True})
    receipt = await mirror.forward_local_submit(adapter, session_id="s1", text="run the red test", submission_id="x")
    assert receipt == {"queued": True, "thread_id": "999"}
    assert _texts(thread) == ["[Cuong] run the red test"]
    adapter.handle_message.assert_awaited_once()  # the forward itself still runs the turn


@pytest.mark.asyncio
async def test_local_forward_without_live_posts_no_echo():
    adapter, thread = _forward_setup({})
    await mirror.forward_local_submit(adapter, session_id="s1", text="run the red test", submission_id="x")
    assert _texts(thread) == []
    adapter.handle_message.assert_awaited_once()


def _forward_setup(extra):
    adapter = _adapter({"session_mirror_local_forward": True, **extra})
    thread = _thread()
    channel = _channel(thread)
    adapter._client = MagicMock()
    adapter._client.get_channel = lambda cid: channel if str(cid) == "555" else None
    adapter._client.get_user = lambda uid: SimpleNamespace(display_name="Cuong")
    adapter.gateway_runner = SimpleNamespace(session_store=SimpleNamespace())
    adapter.handle_message = AsyncMock()
    mirror._save_map(adapter, {"s1": ("999", "Fix the gate")})
    return adapter, thread
