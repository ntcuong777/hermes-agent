"""The Discord session mirror: machine sessions appear as threads.

`session_mirror_channel` (platform extra) names a channel; every NEW titled session of this
machine that is NOT a Discord conversation (CLI, TUI — the `local` origin) gets a thread there,
bound to the session by dispatching the same `/resume <id> --all` pipeline a typed command uses.
The thread is the control surface: the agent answers in it, `/resume` inside switches it.

Thread names carry the machine tag (`[hostname] title`) so several bots sharing one mirror
channel stay distinguishable, and titles sync BOTH ways while the gateway lives: a `/title`
change on the machine renames the thread, a thread rename in Discord renames the session.

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
import logging
import socket
from typing import Any

from gateway.platforms.event import MessageEvent, MessageType
from gateway.session_handoff import check_settled

logger = logging.getLogger(__name__)

POLL_SECONDS = 30.0


def _machine_tag() -> str:
    return (socket.gethostname() or "").split(".")[0]


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


def _threads(adapter: Any) -> dict:
    """sid -> (thread_id, last synced title); lives on the adapter so the thread-rename
    handler can read it. Cleared on restart: rename sync resumes on the next change."""
    mapping = getattr(adapter, "_mirror_threads", None)
    if mapping is None:
        mapping = {}
        adapter._mirror_threads = mapping
    return mapping


async def tick(adapter: Any, seen: dict, *, post_new: bool) -> None:
    """One roster read: seed the seen-map (the first tick after a restart posts nothing —
    existing sessions are not news), open a bound thread for each session that appeared, and
    rename already-mirrored threads whose session was retitled. `seen` maps sid → (thread_id,
    title); pre-existing sessions carry a None thread id, so their rename sync starts once they
    are re-mirrored after a restart."""
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
    if not post_new:  # the seeding tick after a (re)start: existing sessions are not news
        seen.update({sid: (None, title) for sid, title in current.items()})
        return
    tag = _machine_tag()
    threads = _threads(adapter)
    for sid, title in current.items():
        thread_id, synced_title = seen.get(sid, (None, None))
        if sid not in seen:  # new session: open the bound thread (seeded entries are known, not news)
            try:
                await check_settled(db, sid)
            except Exception as exc:  # health: allow BLE001 -- a busy session is retried on a later tick, never fatal
                logger.info("[%s] Session mirror: %s is not settled yet (%s); retried later", adapter.name, sid, exc)
                continue
            name = f"[{tag}] {title or f'session {sid[:8]}'}"[:100]
            try:
                thread = await channel.create_thread(name=name, reason="Hermes session mirror")
            except Exception:  # health: allow BLE001 -- marked seen: a broken channel must not retry-storm every tick
                logger.warning("[%s] Session mirror: could not create a thread for %s", adapter.name, sid, exc_info=True)
                seen[sid] = (None, title)
                continue
            seen[sid] = (str(thread.id), title)
            threads[sid] = (str(thread.id), title)
            mirror_source = adapter.build_source(
                chat_id=str(thread.id), chat_name=title, chat_type="thread", user_id=admin,
                user_name="session mirror", thread_id=str(thread.id), message_id="0",
                guild_id=getattr(getattr(channel, "guild", None), "id", None))
            await adapter.handle_message(MessageEvent(
                text=f"/resume {sid} --all", message_type=MessageType.COMMAND, source=mirror_source,
                user_id=admin, user_name="session mirror", message_id=str(thread.id)))
        elif thread_id is not None and title != synced_title:
            # the session was retitled on the machine: sync out (skipped for seeded entries,
            # whose thread this process never created)
            try:
                thread = channel.get_thread(thread_id) or await adapter._client.fetch_channel(int(thread_id))
                await thread.edit(name=f"[{tag}] {title}"[:100])
                seen[sid] = (thread_id, title)
                threads[sid] = (thread_id, title)
            except Exception:  # health: allow BLE001 -- a refused rename must not stop the roster read
                logger.info("[%s] Session mirror: could not rename the thread of %s", adapter.name, sid, exc_info=True)


async def sync_thread_title(adapter: Any, thread: Any) -> None:
    """A thread rename IN DISCORD renames the session (the reverse direction of the poller's
    title sync), for sessions this gateway has mirrored. Idempotent: the poller's own renames
    echo back through on_thread_update, and a same-name write is skipped."""
    sid = next((s for s, (tid, _t) in _threads(adapter).items() if tid == str(thread.id)), None)
    if sid is None:
        return
    name = (getattr(thread, "name", "") or "").strip()
    if not name or _threads(adapter).get(sid, (None, None))[1] == name:
        return
    runner = getattr(adapter, "gateway_runner", None)
    store = getattr(runner, "session_store", None)
    if store is None:
        return
    try:
        source = adapter.build_source(
            chat_id=str(thread.id), chat_type="thread", thread_id=str(thread.id), message_id="0")
        db = store._db_for_key(runner._session_key_for_source(source))
    except Exception:  # health: allow BLE001 -- a rename sync must not break the adapter's event loop
        return
    try:
        await asyncio.to_thread(db.set_session_title, sid, name)
    except Exception:  # health: allow BLE001 -- sanitize/abuse refusals keep the old title, silently
        logger.info("[%s] Session mirror: Discord rename %r not applied to %s", adapter.name, name, sid, exc_info=True)
        return
    _threads(adapter)[sid] = (str(thread.id), name)


async def loop(adapter: Any) -> None:
    await asyncio.sleep(10)  # let the connect settle before the first (seeding) read
    seen: dict = {}
    seeded = False
    while True:
        try:
            await tick(adapter, seen, post_new=seeded)
            seeded = True
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
