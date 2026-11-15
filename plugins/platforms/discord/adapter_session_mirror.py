"""The Discord session mirror: machine sessions appear as threads.

`session_mirror_channel` (platform extra) names a channel; every NEW titled session of this
machine that is NOT a Discord conversation (CLI, TUI — the `local` origin) gets a thread there,
bound to the session by dispatching the same `/resume <id> --all` pipeline a typed command uses.
The thread is the control surface: the agent answers in it, `/resume` inside switches it.

Design notes:
- Threads are created WITH a starter message: Discord only surfaces a thread in its channel UI
  once it has content, so a bare create_thread would sit invisible in the "active threads" popup.
- The sid → (thread_id, title) map persists in `<home>/cache/session-mirror-threads.json`, so a
  gateway restart (or one of Discord's socket reconnects) keeps the bindings: sessions are
  re-adopted via `client.get_channel(thread_id)` instead of being orphaned, and both title-sync
  directions keep working.
- Thread names carry the machine tag (`[hostname] title`), so several bots sharing one mirror
  channel stay distinguishable. Titles sync BOTH ways: a `/title` change on the machine renames
  the thread, a thread rename in Discord retitles the session. The tag is machine-added and
  machine-stripped — it never enters the session title, so the two sync directions cannot feed
  each other into a rename loop.

Two gates the mirror honours rather than bypasses:
- Cross-origin resume is admin-only by IDOR design (gateway/slash_access.py). A thread source
  resolves the GROUP scope, so the admin id comes from `group_allow_admin_from` — the mirror
  reads that list itself and fails closed (idle, one warning) when it is empty. The synthetic
  turn is a normal (non-internal) event acting as the configured admin: the IDOR gate still
  applies to every real caller.
- A session with unsettled durable work (a live turn, a worker on its lineage) is NOT bound —
  the same settled guard the handoff watcher re-checks — and is retried on later ticks, so a
  busy desk session is mirrored when it settles, never mid-turn.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from pathlib import Path
from typing import Any

from gateway.platforms.event import MessageEvent, MessageType
from gateway.session_handoff import check_settled

logger = logging.getLogger(__name__)

POLL_SECONDS = 10.0
_SETTLE_SECONDS = 3.0
_MAP_FILE_NAME = "session-mirror-threads.json"


def _machine_tag() -> str:
    return (socket.gethostname() or "").split(".")[0]


def _map_path(adapter: Any) -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "cache" / _MAP_FILE_NAME


def _load_map(adapter: Any) -> dict:
    """sid → (thread_id, title), loaded once per adapter from the cache file; the LIVE map both
    sync directions read and write. Persisted so Discord reconnects and gateway restarts keep
    the bindings instead of orphaning every previously mirrored thread."""
    live = getattr(adapter, "_mirror_map", None)
    if live is not None:
        return live
    loaded: dict = {}
    try:
        data = json.loads(_map_path(adapter).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            loaded = {str(k): (str(v[0]), str(v[1])) for k, v in data.items()
                      if isinstance(v, list) and len(v) == 2}
    except FileNotFoundError:
        pass
    except Exception:  # health: allow BLE001 -- a corrupt map must not break the adapter; it rebuilds
        logger.warning("[%s] Session mirror: unreadable map file, starting fresh", adapter.name, exc_info=True)
    adapter._mirror_map = loaded
    return loaded


def _save_map(adapter: Any, seen: dict) -> None:
    try:
        path = _map_path(adapter)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen), encoding="utf-8")
    except Exception:  # health: allow BLE001 -- a failed save costs re-adoption after restart, not correctness
        logger.warning("[%s] Session mirror: could not persist the thread map", adapter.name, exc_info=True)


def _machine_tag() -> str:
    return (socket.gethostname() or "").split(".")[0]


def _tagged(title: str) -> str:
    return f"[{_machine_tag()}] {title}"[:100]


def _backfill_enabled(adapter: Any) -> bool:
    """`session_mirror_backfill`: thread the whole titled roster on the seeding tick too (old
    sessions predate the mirror; without this they never get threads). Default off — it is a
    burst of ~25 threads by design."""
    extra = adapter.config.extra if isinstance(adapter.config.extra, dict) else {}
    return bool(extra.get("session_mirror_backfill"))


def mirror_channel(adapter: Any):
    """The configured mirror channel, resolved against the client cache; None = off."""
    raw = adapter.config.extra.get("session_mirror_channel") if isinstance(adapter.config.extra, dict) else None
    if not raw or not adapter._client:
        return None
    try:
        return adapter._client.get_channel(int(raw))
    except (TypeError, ValueError):
        return None


def mirror_admin_id(adapter: Any) -> str | None:
    """The admin the mirror acts as: the first `group_allow_admin_from` id. A thread source
    resolves the group scope in `_resume_caller_is_admin`, so the DM-scope key does not count."""
    extra = adapter.config.extra if isinstance(adapter.config.extra, dict) else {}
    ids = extra.get("group_allow_admin_from")
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list):
        return None
    cleaned = [str(i).strip() for i in ids if str(i).strip()]
    return cleaned[0] if cleaned else None


def _resolve_thread(adapter: Any, thread_id: str):
    """A bound thread by id: the mirror channel's own thread list first (Discord repopulates it
    after reconnects for visible channels), then the client cache, then an API fetch."""
    channel = mirror_channel(adapter)
    if channel is not None:
        for t in getattr(channel, "threads", []) or []:
            if str(getattr(t, "id", "")) == str(thread_id):
                return t
    client = adapter._client
    if client is None:
        return None
    try:
        channel = client.get_channel(int(thread_id))
        if channel is not None:
            return channel
    except (TypeError, ValueError):
        return None
    try:
        return client.fetch_channel(int(thread_id))
    except Exception:  # health: allow BLE001 -- a deleted thread resolves to None, the map keeps it
        return None


def _starter_message(title: str) -> str:
    return (f"🖥 Mirrored session **{title}** — talking here reaches the agent that runs it on "
            f"this machine. `/resume` switches this thread to another session.")


async def tick(adapter: Any, seen: dict, *, post_new: bool) -> None:
    """One roster read. `seen` is the persisted map (:func:`_load_map`); the first tick after a
    restart re-adopts bound threads (get_channel) and posts nothing — only sessions that appear
    afterwards are mirrored."""
    channel = mirror_channel(adapter)
    admin = mirror_admin_id(adapter)
    if channel is None or not admin:
        if not getattr(adapter, "_mirror_disabled_logged", False):
            adapter._mirror_disabled_logged = True
            logger.warning(
                "[%s] Session mirror idle: set platforms.discord.extra.session_mirror_channel to a "
                "channel ID and list your Discord ID under group_allow_admin_from — cross-origin "
                "/resume is admin-only, and a thread source resolves that group list", adapter.name)
        return
    runner = getattr(adapter, "gateway_runner", None)
    store = getattr(runner, "session_store", None)
    if store is None:
        return
    source = adapter.build_source(
        chat_id=str(channel.id), chat_name=getattr(channel, "name", None), chat_type="dm",
        user_id=admin, message_id="0")
    session_key = runner._session_key_for_source(source)
    db = store._db_for_key(session_key)
    rows = db.list_sessions_rich(
        sources=["local"], limit=25, order_by_last_active=True,
        min_message_count=1, include_children=False)
    current = {str(r.get("id") or ""): str(r.get("title") or "") for r in rows
               if r.get("title") and r.get("id")}
    backfill = _backfill_enabled(adapter)
    if not post_new and not backfill:
        # a fresh install's first tick: seed only, do not mass-thread old sessions
        for sid, title in current.items():
            seen.setdefault(sid, (None, title))
        _save_map(adapter, seen)
        return
    # With backfill on, the seeding tick falls through to the same creation loop as later
    # ticks: every titled session in the roster gets a bound thread, not just sessions that
    # appear later.
    # Bound-session title drift: a mirrored session's source row flips to `discord` once the
    # /resume lane move lands, so the local-sources query above stops returning it. Read each
    # bound session directly and rename its thread on drift — source-agnostic.
    for sid, (thread_id, synced) in list(seen.items()):
        if not thread_id:
            continue
        try:
            row = db.get_session(sid) or {}
        except Exception:  # health: allow BLE001 -- a read miss must not stop the roster tick
            continue
        title = str(row.get("title") or "")
        if not title or title == synced:
            continue
        thread = _resolve_thread(adapter, thread_id)
        if thread is None:  # deleted on Discord: drop the stale binding, keep the session
            seen[sid] = (None, title)
            _save_map(adapter, seen)
            continue
        try:
            await thread.edit(name=_tagged(title))
            seen[sid] = (thread_id, title)
            _save_map(adapter, seen)
        except Exception:  # health: allow BLE001 -- a refused rename must not stop the roster read
            logger.info("[%s] Session mirror: could not rename the thread of %s", adapter.name, sid, exc_info=True)
    for sid, title in current.items():
        thread_id, synced_title = seen.get(sid, (None, None))
        needs_thread = sid not in seen or (backfill and seen[sid][0] is None)
        if needs_thread:  # open the bound thread (with a starter message, so it
            try:          # actually surfaces in the channel instead of the hidden popup)
                await check_settled(db, sid)
            except Exception as exc:  # health: allow BLE001 -- a busy session is retried on a later tick, never fatal
                logger.info("[%s] Session mirror: %s is not settled yet (%s); retried later", adapter.name, sid, exc)
                continue
            try:
                thread = await channel.create_thread(name=_tagged(title), reason="Hermes session mirror")
                await thread.send(_starter_message(title))
            except Exception:  # health: allow BLE001 -- marked seen: a broken channel must not retry-storm every tick
                logger.warning("[%s] Session mirror: could not create a thread for %s", adapter.name, sid, exc_info=True)
                seen[sid] = (None, title)
                _save_map(adapter, seen)
                continue
            seen[sid] = (str(thread.id), title)
            _save_map(adapter, seen)
            mirror_source = adapter.build_source(
                chat_id=str(thread.id), chat_name=title, chat_type="thread", user_id=admin,
                user_name="session mirror", thread_id=str(thread.id), message_id="0",
                guild_id=getattr(getattr(channel, "guild", None), "id", None))
            await adapter.handle_message(MessageEvent(
                text=f"/resume {sid} --all", message_type=MessageType.COMMAND, source=mirror_source,
                user_id=admin, user_name="session mirror", message_id=str(thread.id)))
        elif title != synced_title:
            # the session was retitled on the machine: rename its thread (resolved through the
            # client cache / API — not the per-channel thread list, which reconnects reset)
            thread = _resolve_thread(adapter, thread_id)
            if thread is None:
                logger.info("[%s] Session mirror: thread for %s (id %s) is gone; unbinding", adapter.name, sid, thread_id)
                seen[sid] = (None, title)
                _save_map(adapter, seen)
                continue
            try:
                await thread.edit(name=_tagged(title))
                seen[sid] = (thread_id, title)
                _save_map(adapter, seen)
            except Exception:  # health: allow BLE001 -- a refused rename must not stop the roster read
                logger.info("[%s] Session mirror: could not rename the thread of %s", adapter.name, sid, exc_info=True)


async def sync_thread_title(adapter: Any, thread: Any) -> None:
    """A thread rename IN DISCORD retitles the session (the reverse direction of the poller's
    title sync), for sessions this gateway has mirrored. The machine tag is stripped before the
    write — it never enters the session title — and a same-name echo (the poller's own rename
    arriving back through on_thread_update) is skipped, which is what keeps the two directions
    from feeding each other."""
    tag_prefix = f"[{_machine_tag()}] "
    name = (getattr(thread, "name", "") or "").strip()
    if name.startswith(tag_prefix):
        stripped = name[len(tag_prefix):].strip()
    else:
        stripped = name
    if not stripped:
        return
    seen = _load_map(adapter)
    sid, _thread_id, synced_title = next(
        ((s, tid, t) for s, (tid, t) in seen.items() if tid == str(thread.id)),
        (None, None, None))
    if sid is None or stripped == synced_title:
        return  # an unbound thread, or the echo of the poller's own rename
    runner = getattr(adapter, "gateway_runner", None)
    store = getattr(runner, "session_store", None)
    if store is None:
        return
    admin = mirror_admin_id(adapter)
    try:
        source = adapter.build_source(
            chat_id=str(thread.id), chat_type="thread", thread_id=str(thread.id), message_id="0",
            user_id=admin)
        db = store._db_for_key(runner._session_key_for_source(source))
    except Exception:  # health: allow BLE001 -- a rename sync must not break the adapter's event loop
        return
    try:
        await asyncio.to_thread(db.set_session_title, sid, stripped)
    except ValueError as exc:  # a duplicate title: refused by the store; the thread keeps the name
        logger.info("[%s] Session mirror: Discord rename not applied to %s (%s)", adapter.name, sid, exc)
        return
    except Exception:  # health: allow BLE001 -- other refusals keep the old title, silently
        logger.info("[%s] Session mirror: Discord rename %r not applied to %s", adapter.name, name, sid, exc_info=True)
        return
    seen[sid] = (str(thread.id), stripped)
    _save_map(adapter, seen)


async def loop(adapter: Any) -> None:
    await asyncio.sleep(_SETTLE_SECONDS)  # let the connect settle before the first read
    logger.info("[%s] Session mirror task started: channel=%r admin=%s",
                adapter.name, mirror_channel(adapter), mirror_admin_id(adapter) or "NOT CONFIGURED")
    # A persisted map means this boot continues a previous mirror: sessions that appeared during
    # the downtime are genuinely new and get threads. A fresh install (no map) seeds only, so the
    # first boot does not mass-thread every historical session.
    fresh = not _map_path(adapter).exists()
    first = True
    while True:
        try:
            await tick(adapter, _load_map(adapter), post_new=not first or not fresh)
            first = False
        except asyncio.CancelledError:
            raise
        except Exception:  # health: allow BLE001 -- the mirror is ambient; a bad tick must not kill the loop
            logger.warning("[%s] Session mirror tick failed", adapter.name, exc_info=True)
        await asyncio.sleep(POLL_SECONDS)


def start(adapter: Any) -> None:
    """Start the mirror poll task (idempotent); the channel must be configured for it to post."""
    task = getattr(adapter, "_mirror_task", None)
    if task and not task.done():
        return
    adapter._mirror_task = asyncio.create_task(loop(adapter))
