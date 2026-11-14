"""The Discord session mirror: new machine-local sessions (CLI/TUI — the `local` origin; api
sessions are NOT mirrored) automatically get a thread in the mirror channel, bound to the session
by the same `/resume <id> --all` pipeline a typed command uses. The synthetic turn is a normal
(non-internal) event acting as the configured `group_allow_admin_from` admin — a thread source
resolves the group scope, so the DM-scope key does not count, and the mirror fails closed without
it. First tick after a (re)start only seeds (re-adopting surviving threads by their tagged name);
a session with unsettled durable work is retried later, never bound mid-turn. Titles sync BOTH
ways, and the machine tag never enters the session title — so the two sync directions cannot
feed each other into a rename loop.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig

from plugins.platforms.discord.adapter import DiscordAdapter
from plugins.platforms.discord import adapter_session_mirror


def _adapter(extra=None):
    return DiscordAdapter(PlatformConfig(enabled=True, token="fake-token", extra=extra or {}))


def _row(sid, title, source="local"):
    return {"id": sid, "title": title, "source": source}


def _thread_named(name):
    return SimpleNamespace(id=999, name=name, edit=AsyncMock())


def _mirror_channel():
    """A mirror channel whose thread list is inspectable (seeding adopts by tagged name) and
    whose create_thread hands back a concrete thread (id 999) the tests can assert against."""
    channel = MagicMock()
    channel.id = 555
    channel.name = "sessions"
    channel.guild.id = 1
    channel.threads = []
    channel.create_thread = AsyncMock(return_value=SimpleNamespace(id=999, name="t"))
    return channel


def _wired_adapter(monkeypatch, rows, *, channel=None, extra=None):
    """Adapter whose mirror reads `rows` from a stubbed session db honouring the `sources`
    filter, against a REAL mirror resolution (config extra + client cache). `rows` may be
    replaced between ticks via `holder["rows"]`; `titles` captures session retitle writes."""
    base = {"session_mirror_channel": "555", "group_allow_admin_from": ["admin-1"]}
    base.update(extra or {})
    adapter = _adapter(base)
    holder = {"rows": list(rows)}
    titles: list = []

    async def tick(post_new):
        await adapter_session_mirror.tick(adapter, adapter_session_mirror._seen(adapter), post_new=post_new)

    def db(**kw):
        sources = kw.get("sources")
        return [r for r in holder["rows"] if not sources or r.get("source", "local") in sources]

    def set_session_title(sid, name):
        if holder.get("refuse_title") == name:
            raise ValueError("duplicate title")
        titles.append((sid, name))

    dbs: dict = {}
    store = SimpleNamespace(_db_for_key=lambda key: dbs.setdefault(key, SimpleNamespace(
        list_sessions_rich=db, set_session_title=set_session_title)))
    adapter.gateway_runner = SimpleNamespace(
        session_store=store, _session_key_for_source=lambda source: "agent:main:discord:dm:555",
        _profile_name_for_source=lambda source, adapter_profile=None: "main")
    adapter.handle_message = AsyncMock()
    if channel is None:
        channel = _mirror_channel()
    adapter._client = MagicMock()
    adapter._client.get_channel = lambda cid: channel if str(cid) == "555" else None
    return adapter, tick, channel, holder, titles


def _seen(adapter):
    return adapter_session_mirror._seen(adapter)


@pytest.mark.asyncio
async def test_seeding_tick_creates_no_threads_and_fills_seen(monkeypatch):
    adapter, tick, channel, _holder, _titles = _wired_adapter(
        monkeypatch, [_row("s1", "Fix the gate"), _row("s2", "Review PR")])

    await tick(post_new=False)

    channel.create_thread.assert_not_called()
    adapter.handle_message.assert_not_called()
    assert _seen(adapter) == {
        "s1": (None, "Fix the gate"), "s2": (None, "Review PR")}


@pytest.mark.asyncio
async def test_new_session_opens_a_thread_and_resumes_into_it(monkeypatch):
    adapter, tick, _channel, _holder, _titles = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)  # seed on an empty machine

    _holder["rows"] = [_row("s1", "Fix the gate")]  # a session started at the desk
    await tick(post_new=True)

    _channel.create_thread.assert_awaited_once()
    name = _channel.create_thread.await_args.kwargs["name"]
    assert "Fix the gate" in name and name.startswith("[")  # the machine tag prefixes the title
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.text == "/resume s1 --all"
    assert event.internal is False  # the IDOR gate stays real: no impersonation by internal bypass
    assert event.source.user_id == "admin-1"
    assert event.source.thread_id == "999"
    assert _seen(adapter) == {"s1": ("999", "Fix the gate")}


@pytest.mark.asyncio
async def test_state_only_changes_and_removals_post_nothing(monkeypatch):
    adapter, tick, _channel, _holder, _titles = _wired_adapter(monkeypatch, [_row("s1", "Fix the gate")])
    await tick(post_new=False)

    await tick(post_new=True)  # same session: no post
    _channel.create_thread.assert_not_called()
    adapter.handle_message.assert_not_called()

    await tick(post_new=True)  # session gone: no post either (removals are not news)
    _channel.create_thread.assert_not_called()


@pytest.mark.asyncio
async def test_untitled_and_discord_sourced_rows_are_skipped(monkeypatch):
    """Untitled rows never mirror; Discord conversations are excluded by the sources filter."""
    adapter, tick, _channel, holder, _titles = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)  # seed on an empty machine

    holder["rows"] = [_row("s1", ""), _row("s2", "Fix the gate"), _row("s3", "Fix the gate", source="discord")]
    await tick(post_new=True)

    # s1 has no title, s3 is a Discord conversation: only s2 opens a thread.
    _channel.create_thread.assert_awaited_once()
    assert _seen(adapter) == {"s2": ("999", "Fix the gate")}


@pytest.mark.asyncio
async def test_unsettled_session_is_not_bound_but_retried(monkeypatch):
    """A session with durable work (a live turn) is left alone this tick — the settled guard —
    and mirrored on a later tick once it settles."""
    adapter, tick, channel, holder, _titles = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)

    holder["rows"] = [_row("s1", "Fix the gate")]

    async def refusing(db, sid):
        raise RuntimeError("wait for the current turn to finish, then retry the handoff")
    monkeypatch.setattr(adapter_session_mirror, "check_settled", refusing)
    await tick(post_new=True)
    channel.create_thread.assert_not_called()  # not bound mid-turn
    adapter.handle_message.assert_not_called()
    assert _seen(adapter) == {}  # retried later

    monkeypatch.setattr(adapter_session_mirror, "check_settled", AsyncMock())
    await tick(post_new=True)
    channel.create_thread.assert_awaited_once()  # settles -> mirrored
    assert _seen(adapter) == {"s1": ("999", "Fix the gate")}


@pytest.mark.asyncio
async def test_thread_creation_failure_marks_seen_no_retry_storm(monkeypatch):
    _adapter_obj, tick, _channel, holder, _titles = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)
    holder["rows"] = [_row("s1", "Fix the gate")]
    _channel.create_thread = AsyncMock(side_effect=RuntimeError("missing permissions"))

    await tick(post_new=True)
    await tick(post_new=True)
    _channel.create_thread.assert_awaited_once()  # marked seen: no retry storm


@pytest.mark.asyncio
async def test_missing_group_admin_list_fails_closed(monkeypatch):
    """No group_allow_admin_from -> the mirror idles: cross-origin /resume is admin-only and a
    thread source resolves the GROUP list, so the DM-scope key does not count."""
    channel = _mirror_channel()
    adapter, tick, _channel_state, _holder, _titles = _wired_adapter(
        monkeypatch, [_row("s1", "Fix the gate")], channel=channel,
        extra={"group_allow_admin_from": []})
    await tick(post_new=False)
    await tick(post_new=True)
    channel.create_thread.assert_not_called()
    adapter.handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_no_mirror_channel_configured_stays_idle(monkeypatch):
    adapter = _adapter({"group_allow_admin_from": ["admin-1"]})
    adapter.gateway_runner = SimpleNamespace(session_store=None)
    adapter.handle_message = AsyncMock()
    adapter._client = MagicMock()
    adapter._client.get_channel = lambda cid: None  # nothing cached: channel None
    await adapter_session_mirror.tick(adapter, adapter_session_mirror._seen(adapter), post_new=True)
    adapter.handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_machine_retitle_renames_the_thread(monkeypatch):
    """A /title change on the machine renames the mirrored thread (machine → Discord sync)."""
    _adapter_obj, tick, channel, holder, _titles = _wired_adapter(monkeypatch, [])
    thread = _thread_named("[cuong-page-mac] Fix the gate")
    channel.create_thread = AsyncMock(return_value=thread)
    channel.threads = [thread]
    await tick(post_new=False)
    holder["rows"] = [_row("s1", "Fix the gate")]
    await tick(post_new=True)

    holder["rows"] = [_row("s1", "Fix the gate (done)")]
    await tick(post_new=True)

    thread.edit.assert_awaited_once()
    assert thread.edit.await_args.kwargs["name"] == "[cuong-page-mac] Fix the gate (done)"


@pytest.mark.asyncio
async def test_discord_thread_rename_renames_the_session_and_strips_the_tag(monkeypatch):
    """The reverse direction: renaming the thread in Discord retitles the session (Discord →
    machine) with the tag STRIPPED — the tag is machine-added and must never enter the title,
    or the poller would re-rename and the two directions would feed each other."""
    adapter, tick, _channel, _holder, titles = _wired_adapter(monkeypatch, [])
    thread = _thread_named("[cuong-page-mac] Fix the gate")
    _channel.threads = [thread]
    await tick(post_new=False)
    _holder["rows"] = [_row("s1", "Fix the gate")]
    await tick(post_new=True)

    thread.name = "[cuong-page-mac] ship it"
    await adapter_session_mirror.sync_thread_title(adapter, thread)
    assert titles == [("s1", "ship it")]  # tag stripped: the session title stays clean
    assert _seen(adapter) == {"s1": ("999", "ship it")}

    # the echo of this rename (same name again) writes nothing — no loop
    await adapter_session_mirror.sync_thread_title(adapter, thread)
    assert titles == [("s1", "ship it")]


@pytest.mark.asyncio
async def test_unbound_thread_rename_is_ignored(monkeypatch):
    """Threads the mirror never created are not renamed into sessions."""
    adapter, _tick, _channel, _holder, titles = _wired_adapter(monkeypatch, [])
    await adapter_session_mirror.sync_thread_title(adapter, _thread_named("not ours"))
    assert titles == []


@pytest.mark.asyncio
async def test_seeding_adopts_a_surviving_thread_so_sync_survives_restarts(monkeypatch):
    """After a restart the mirror re-adopts the thread a previous life created (matched by its
    tagged name), so both-way title sync keeps working instead of dying with the old process."""
    _adapter_obj, tick, channel, _holder, titles = _wired_adapter(
        monkeypatch, [_row("s1", "Fix the gate")])
    surviving = _thread_named(f"[{adapter_session_mirror._machine_tag()}] Fix the gate")
    channel.threads = [surviving]
    await tick(post_new=False)  # the seeding adopt: the session existed before the restart

    await tick(post_new=True)  # known AND adopted: no new thread
    channel.create_thread.assert_not_called()

    # Discord-side rename sync works on the adopted thread
    surviving.name = "[cuong-page-mac] ship it"
    await adapter_session_mirror.sync_thread_title(_adapter_obj, surviving)
    assert titles == [("s1", "ship it")]


@pytest.mark.asyncio
async def test_duplicate_title_refusal_keeps_the_old_title_without_a_loop(monkeypatch):
    """A duplicate-title refusal (titles are unique in the store) writes nothing and leaves the
    mapping alone: the thread keeps its name and no rename loop starts."""
    _adapter_obj, tick, _channel, _holder, titles = _wired_adapter(monkeypatch, [])
    thread = _thread_named("[cuong-page-mac] Fix the gate")
    _channel.threads = [thread]
    await tick(post_new=False)
    _holder["rows"] = [_row("s1", "Fix the gate")]
    await tick(post_new=True)

    thread.name = "[cuong-page-mac] taken"

    assert titles == []  # nothing written
