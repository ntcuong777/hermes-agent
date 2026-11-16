"""The session mirror's live feed (`session_mirror_live`): a mirrored thread follows its session.

Without it a mirrored thread shows only what the bound Discord lane posts natively: nothing while
the session still runs on its local (TUI/CLI) lane, and never the desk's own prompts, because a
forwarded or viewer-submitted input is not a Discord message. The feed subscribes to the session's
canonical event stream exactly like a read-only viewer (an ``event_stream.observers`` entry, the
same hook the API run projection uses) and posts display-only bot messages; it never admits a turn.

What posts what, per turn (keyed by its admission id):
- ``message.start`` of a session-viewer input (``is_viewer_input``: a TUI/CLI prompt, never a
  Discord message, a producer/automation wake or the mirror's own ``/resume``) echoes the prompt as
  ``[<admin display name>] text``. A prompt relayed by ``forward_local_submit`` is a native Discord
  admission and is echoed by the forward itself, so it is never echoed twice.
- While the lane is LOCAL (before the binding ``/resume``, or a busy session still waiting to
  settle) nothing reaches the thread natively, so the feed also posts the turn: tool activity as ONE
  status message edited in place (coalesced, at most one edit per ``_STATUS_EDIT_SECONDS``),
  commentary (``message.interim``), and the final reply (``message.complete``, falling back to the
  streamed text when the frame carries none), split at Discord's 2000-char limit.
- Once the lane is a messaging route (bound), the lane's own stream consumer posts replies and tool
  progress into the thread: the feed only echoes viewer prompts and posts nothing else.

A newly created thread first gets a recap of the stored conversation (user + assistant turns, the
last ``_RECAP_MAX_MESSAGES``); the in-flight turn's frames are replayed from the stream's bounded
ring. Recap and live posts dedupe against the last user prompt and the last reply posted per
session, which absorbs the race between the transcript read and the subscription.

Frames arrive on the publisher's thread under the stream lock: the observer only hands them to
the loop (``call_soon_threadsafe``); one worker task posts them in order.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Minimum spacing of the edit-in-place tool status message (Discord allows ~5 edits / 5s per channel).
_STATUS_EDIT_SECONDS = 2.5
#: Stored messages a fresh thread's recap shows (older ones are summarized as a count).
_RECAP_MAX_MESSAGES = 20
#: One recap entry is one Discord message: longer entries are cut (live replies split instead).
_RECAP_ENTRY_CHARS = 1900
_DISCORD_LIMIT = 2000
#: Frame types the feed reads; every other frame is ignored at the observer.
_FEED_TYPES = frozenset({"message.start", "message.delta", "message.interim", "message.complete",
                         "tool.start", "tool.complete"})


def live_enabled(adapter: Any) -> bool:
    """`session_mirror_live` (platform extra): the thread follows its session live. Default off."""
    extra = adapter.config.extra if isinstance(adapter.config.extra, dict) else {}
    return bool(extra.get("session_mirror_live"))


@dataclass
class _Turn:
    full: bool  # the lane is local: the feed carries the whole turn, not only the prompt echo
    tools: int = 0
    errors: int = 0
    last_tool: str = ""
    status_msg: Any = None
    status_dirty: bool = False
    status_at: float = 0.0
    segments: list = field(default_factory=lambda: [""])
    interims: list = field(default_factory=list)


@dataclass
class _Attachment:
    authority: Any
    live: Any
    stream: Any
    observer: Any


@dataclass
class _Feed:
    loop: Any
    queue: asyncio.Queue
    worker: Any = None
    attachments: dict = field(default_factory=dict)  # sid -> _Attachment
    turns: dict = field(default_factory=dict)  # (sid, admission_id) -> _Turn
    last_user: dict = field(default_factory=dict)  # sid -> last prompt text shown in the thread
    last_reply: dict = field(default_factory=dict)  # sid -> last reply text shown in the thread
    threads: dict = field(default_factory=dict)  # thread id -> resolved thread


def _feed(adapter: Any) -> _Feed:
    """The adapter's feed state, created (with its worker) on first use inside the running loop."""
    feed = getattr(adapter, "_mirror_live", None)
    if feed is None:
        feed = adapter._mirror_live = _Feed(loop=asyncio.get_running_loop(), queue=asyncio.Queue())
    if feed.worker is None or feed.worker.done():
        feed.worker = asyncio.create_task(_worker(adapter, feed))
    return feed


def stop(adapter: Any) -> None:
    """Detach every observer and stop the worker (the mirror loop's exit)."""
    feed = getattr(adapter, "_mirror_live", None)
    if feed is None:
        return
    for attachment in feed.attachments.values():
        with attachment.stream.lock:
            attachment.stream.observers.discard(attachment.observer)
    feed.attachments.clear()
    if feed.worker is not None and not feed.worker.done():
        feed.worker.cancel()
    adapter._mirror_live = None


def _observer(feed: _Feed, sid: str):
    loop, queue = feed.loop, feed.queue

    def observe(frame):
        params = frame.get("params") if isinstance(frame, dict) else None
        if not isinstance(params, dict) or params.get("type") not in _FEED_TYPES:
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, (sid, "frame", params))
        except RuntimeError:  # the loop closed under a late publisher: nothing left to post to
            return
    return observe


def _live_session(adapter: Any, sid: str):
    """``(authority, key, LiveSession)`` currently running *sid* (its lineage root's entry), else None."""
    from gateway.session_authorities import active_authority
    authority = active_authority(getattr(adapter, "gateway_runner", None))
    sessions = getattr(authority, "sessions", None)
    if not isinstance(sessions, dict):
        return None
    key = sid if sid in sessions else authority.logical_owner(sid)
    live = sessions.get(key)
    return (authority, key, live) if live is not None else None


def _replay(stream: Any, *, only_admission: str | None) -> list:
    """Ring frames of *stream* (caller holds its lock): all of them, or one admission's."""
    replay = stream.since(stream.epoch, 0)
    if replay.get("snapshot_required"):
        return []
    frames = [f for f in replay.get("events") or () if f.get("type") in _FEED_TYPES]
    if only_admission is None:
        return frames
    return [f for f in frames if f.get("admission_id") == only_admission]


def _attach(adapter: Any, feed: _Feed, sid: str, *, fresh_thread: bool) -> None:
    """Subscribe *sid*'s current stream (idempotent). A replaced stream (the binding moved the
    lane, a retirement restored it) replays its ring; a fresh thread replays the in-flight turn."""
    found = _live_session(adapter, sid)
    if found is None:
        return
    authority, key, live = found
    stream = live.event_stream
    prior = feed.attachments.get(sid)
    if prior is not None and prior.stream is stream:
        return
    observer = _observer(feed, sid)
    with stream.lock:
        if prior is not None:
            replay = _replay(stream, only_admission=None)
        elif fresh_thread and authority.executing.get(key) is not None:
            running = (stream.execution or {}).get("admission_id")
            replay = _replay(stream, only_admission=running) if running else []
        else:
            replay = []
        stream.observers.add(observer)
    if prior is not None:
        with prior.stream.lock:
            prior.stream.observers.discard(prior.observer)
    feed.attachments[sid] = _Attachment(authority, live, stream, observer)
    for frame in replay:
        feed.queue.put_nowait((sid, "frame", frame))


def start_feed(adapter: Any, sid: str, db: Any) -> None:
    """A thread was just created for *sid*: recap its conversation, then follow it live."""
    feed = _feed(adapter)
    feed.queue.put_nowait((sid, "recap", db))
    try:
        _attach(adapter, feed, sid, fresh_thread=True)
    except Exception:  # health: allow BLE001 -- a failed subscription leaves the thread static; the next tick retries
        logger.warning("[%s] Session mirror live: could not follow %s", adapter.name, sid, exc_info=True)


def ensure_feeds(adapter: Any, seen: dict) -> None:
    """Every mirrored session with a thread is followed through its CURRENT stream (a tick-time
    re-check: the binding and retirements replace a session's live entry)."""
    feed = _feed(adapter)
    for sid, (thread_id, _title) in list(seen.items()):
        if not thread_id:
            continue
        try:
            _attach(adapter, feed, sid, fresh_thread=False)
        except Exception:  # health: allow BLE001 -- one unreadable session must not stop the others
            logger.info("[%s] Session mirror live: could not follow %s", adapter.name, sid, exc_info=True)


# ── posting ────────────────────────────────────────────────────────────────────────────────────


async def _thread_for(adapter: Any, feed: _Feed, sid: str):
    from plugins.platforms.discord.adapter_session_mirror import _load_map, _resolve_thread
    binding = _load_map(adapter).get(sid)
    thread_id = binding[0] if binding else None
    if not thread_id:
        return None
    thread = feed.threads.get(thread_id)
    if thread is None:
        thread = _resolve_thread(adapter, thread_id)
        if inspect.isawaitable(thread):
            thread = await thread
        if thread is not None:
            feed.threads[thread_id] = thread
    return thread


async def _send(adapter: Any, thread: Any, text: str):
    """Post *text*, split at Discord's limit; returns the last message sent."""
    sent = None
    for chunk in adapter.truncate_message(text, _DISCORD_LIMIT):
        sent = await thread.send(chunk)
    return sent


async def _sender_label(adapter: Any) -> str:
    from gateway.platforms.helpers import tag_safe_display_name
    from plugins.platforms.discord.adapter_session_mirror import _forward_sender_name, _machine_tag, mirror_admin_id
    admin = mirror_admin_id(adapter)
    name = await _forward_sender_name(adapter, admin) if admin else None
    return tag_safe_display_name(name or _machine_tag() or "local")


async def echo_prompt(adapter: Any, thread: Any, text: str) -> None:
    """A local prompt shown in the thread the way a shared session labels its sender."""
    await _send(adapter, thread, f"[{await _sender_label(adapter)}] {text}")


def _text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(str(p.get("text") or "") for p in content
                            if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
    return content.strip() if isinstance(content, str) else ""


def _clip(text: str) -> str:
    return text if len(text) <= _RECAP_ENTRY_CHARS else text[:_RECAP_ENTRY_CHARS] + " … (cut)"


async def _recap(adapter: Any, feed: _Feed, sid: str, db: Any) -> None:
    from plugins.platforms.discord.adapter_session_mirror import _live_lineage_sid
    thread = await _thread_for(adapter, feed, sid)
    if thread is None or db is None:
        return

    def read():
        tip = _live_lineage_sid(db, sid)
        return db.get_messages_as_conversation(tip, include_ancestors=True, include_compacted=True)
    history = [(m.get("role"), _text_of(m)) for m in await asyncio.to_thread(read)
               if m.get("role") in ("user", "assistant")]
    history = [(role, text) for role, text in history if text]
    shown = history[-_RECAP_MAX_MESSAGES:]
    if len(history) > len(shown):
        await thread.send(f"… {len(history) - len(shown)} earlier messages not shown")
    label = await _sender_label(adapter) if any(role == "user" for role, _ in shown) else ""
    for role, text in shown:
        await thread.send(_clip(f"[{label}] {text}" if role == "user" else text))
        (feed.last_user if role == "user" else feed.last_reply)[sid] = text


def _status_text(turn: _Turn, *, final: bool) -> str:
    calls = f"{turn.tools} tool call{'s' if turn.tools != 1 else ''}"
    failed = f", {turn.errors} failed" if turn.errors else ""
    if final:
        return f"✓ {calls}{failed}"
    return f"⚙️ working… {calls}{failed} · last: {turn.last_tool}"[:_DISCORD_LIMIT]


async def _flush_status(adapter: Any, feed: _Feed, sid: str, turn: _Turn, *, final: bool = False) -> None:
    if not turn.full or not turn.tools or not (turn.status_dirty or final):
        return
    thread = await _thread_for(adapter, feed, sid)
    if thread is None:
        return
    text = _status_text(turn, final=final)
    if turn.status_msg is None:
        turn.status_msg = await thread.send(text)
    else:
        await turn.status_msg.edit(content=text)
    turn.status_dirty, turn.status_at = False, time.monotonic()


async def _flush_due(adapter: Any, feed: _Feed) -> None:
    now = time.monotonic()
    for (sid, _aid), turn in list(feed.turns.items()):
        if turn.status_dirty and now - turn.status_at >= _STATUS_EDIT_SECONDS:
            await _flush_status(adapter, feed, sid, turn)


def _next_due(feed: _Feed) -> float | None:
    dirty = [t.status_at + _STATUS_EDIT_SECONDS for t in feed.turns.values() if t.status_dirty]
    return max(0.0, min(dirty) - time.monotonic()) if dirty else None


def _lane_is_local(feed: _Feed, sid: str) -> bool:
    from gateway.config import Platform
    attachment = feed.attachments.get(sid)
    source = getattr(getattr(attachment, "live", None), "source", None)
    return getattr(source, "platform", None) == Platform.LOCAL


async def _on_start(adapter: Any, feed: _Feed, sid: str, admission_id: str) -> None:
    from gateway.session_operator import is_viewer_input
    from hermes_state_runtime import get_session_admission
    feed.turns[(sid, admission_id)] = _Turn(full=_lane_is_local(feed, sid))
    attachment = feed.attachments.get(sid)
    if attachment is None:
        return
    row = await asyncio.to_thread(get_session_admission, attachment.authority.db, admission_id=admission_id)
    if row is None or not is_viewer_input(row):
        return  # a Discord message, a forward (echoed by the forward), an automation wake
    text = str(row["payload"].get("text") or "").strip()
    if not text or text == feed.last_user.get(sid):
        return
    thread = await _thread_for(adapter, feed, sid)
    if thread is not None:
        await echo_prompt(adapter, thread, text)
        feed.last_user[sid] = text


async def _post_reply(adapter: Any, feed: _Feed, sid: str, turn: _Turn, text: str) -> None:
    text = text.strip()
    if not text or text == feed.last_reply.get(sid) or text in turn.interims:
        return
    thread = await _thread_for(adapter, feed, sid)
    if thread is not None:
        await _send(adapter, thread, text)
        feed.last_reply[sid] = text


async def _on_complete(adapter: Any, feed: _Feed, sid: str, turn: _Turn, payload: dict) -> None:
    await _flush_status(adapter, feed, sid, turn, final=True)
    reply = payload.get("text") or payload.get("content") or next(
        (s for s in reversed(turn.segments) if s.strip()), "")
    if isinstance(reply, str) and reply.strip():
        await _post_reply(adapter, feed, sid, turn, reply)
    elif payload.get("status") not in (None, "complete"):
        reason = payload.get("warning") or payload.get("failure_reason") or payload.get("outcome") or payload["status"]
        thread = await _thread_for(adapter, feed, sid)
        if thread is not None:
            await thread.send(f"⚠️ Turn ended without a reply: {reason}"[:_DISCORD_LIMIT])


def _on_delta(turn: _Turn, text: Any) -> None:
    """Streamed reply text; ``None`` closes the segment (a local lane's break before tool lines)."""
    if text is None:
        turn.segments.append("")
    else:
        turn.segments[-1] += str(text)


async def _on_frame(adapter: Any, feed: _Feed, sid: str, params: dict) -> None:
    kind = params.get("type")
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    admission_id = params.get("admission_id") or payload.get("admission_id")
    if not admission_id:
        return
    key = (sid, admission_id)
    if kind == "message.start":
        await _on_start(adapter, feed, sid, admission_id)
        return
    turn = feed.turns.get(key)
    if turn is None:  # joined mid-turn (truncated replay): carry what is left of it
        turn = feed.turns[key] = _Turn(full=_lane_is_local(feed, sid))
    if kind == "message.complete":
        feed.turns.pop(key, None)
        if turn.full:
            await _on_complete(adapter, feed, sid, turn, payload)
        return
    if not turn.full:
        return  # bound lane: it streams its own replies and tool progress into the thread
    if kind == "tool.start":
        turn.tools += 1
        turn.last_tool = f"`{payload.get('name') or 'tool'}` {str(payload.get('context') or '')[:80]}".strip()
        turn.status_dirty = True
        if time.monotonic() - turn.status_at >= _STATUS_EDIT_SECONDS:
            await _flush_status(adapter, feed, sid, turn)
    elif kind == "tool.complete" and payload.get("is_error"):
        turn.errors += 1
        turn.status_dirty = True
    elif kind == "message.delta":
        _on_delta(turn, payload.get("text"))
    elif kind == "message.interim" and str(payload.get("text") or "").strip():
        text = str(payload["text"]).strip()
        await _post_reply(adapter, feed, sid, turn, text)
        turn.interims.append(text)


async def _worker(adapter: Any, feed: _Feed) -> None:
    while True:
        try:
            sid, kind, data = await asyncio.wait_for(feed.queue.get(), _next_due(feed))
        except TimeoutError:
            await _flush_due(adapter, feed)
            continue
        try:
            if kind == "recap":
                await _recap(adapter, feed, sid, data)
            else:
                await _on_frame(adapter, feed, sid, data)
            await _flush_due(adapter, feed)
        except asyncio.CancelledError:
            raise
        except Exception:  # health: allow BLE001 -- the feed is display-only; one failed post must not stop it
            logger.warning("[%s] Session mirror live: could not post %s for %s", adapter.name, kind, sid,
                           exc_info=True)
        finally:
            feed.queue.task_done()
