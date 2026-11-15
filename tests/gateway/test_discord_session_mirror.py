"""The Discord session mirror: new machine-local sessions (CLI/TUI — the `local` origin; api
sessions are NOT mirrored) automatically get a thread in the mirror channel, bound to the session
by the same `/resume <id> --all` pipeline a typed command uses. The synthetic turn is a normal
(non-internal) event acting as the configured `group_allow_admin_from` admin — a thread source
resolves the group scope, so the DM-scope key does not count, and the mirror fails closed without
it. Threads carry a starter message (an empty thread sits in Discord's hidden popup, not in the
channel) and a machine-tagged name; the sid → thread map persists in the Hermes cache, so
restarts and reconnects re-adopt bindings instead of orphaning them. Titles sync BOTH ways, the
tag never enters the session title, and the two sync directions share one map — so they cannot
feed each other into a rename loop. A session with unsettled durable work is retried later,
never bound mid-turn.
"""

import json
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


def _thread_named(name, send=None):
    thread = SimpleNamespace(id=999, name=name, edit=AsyncMock(), send=send or AsyncMock())
    return thread


def _mirror_channel():
    """A mirror channel whose thread list is inspectable and whose create_thread hands back a
    concrete thread the tests can assert against."""
    channel = MagicMock()
    channel.id = 555
    channel.name = "sessions"
    channel.guild.id = 1
    channel.threads = []
    channel.create_thread = AsyncMock(return_value=_thread_named("[h] t"))
    return channel


def _wired_adapter(monkeypatch, rows, *, channel=None, extra=None):
    """Adapter whose mirror reads `rows` from a stubbed session db honouring the `sources`
    filter, against a REAL mirror resolution (config extra + client cache). `rows` may be
    replaced between ticks via `holder["rows"]`; `titles` captures session retitle writes;
    `seen` is the persisted map (tests read it through `adapter_session_mirror._load_map`)."""
    base = {"session_mirror_channel": "555", "group_allow_admin_from": ["admin-1"]}
    base.update(extra or {})
    adapter = _adapter(base)
    holder = {"rows": list(rows)}
    titles: list = []

    async def tick(post_new):
        await adapter_session_mirror.tick(adapter, adapter_session_mirror._load_map(adapter), post_new=post_new)

    def db(**kw):
        sources = kw.get("sources")
        return [r for r in holder["rows"] if not sources or r.get("source", "local") in sources]

    def get_session(sid):
        for r in holder["rows"]:
            if r.get("id") == sid:
                return dict(r)
        return None

    dbs: dict = {}
    store = SimpleNamespace(_db_for_key=lambda key: dbs.setdefault(key, SimpleNamespace(
        list_sessions_rich=db, set_session_title=lambda sid, name: titles.append((sid, name)),
        get_session=get_session)))
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
    return adapter_session_mirror._load_map(adapter)


def _map_file(monkeypatch):
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "cache" / "session-mirror-threads.json"


@pytest.mark.asyncio
async def test_seeding_tick_creates_no_threads_on_a_fresh_install(monkeypatch):
    """No map file = fresh install: the first tick seeds only, so historical sessions are not
    mass-threaded; the map file exists afterwards."""
    adapter, tick, channel, _holder, _titles = _wired_adapter(
        monkeypatch, [_row("s1", "Fix the gate"), _row("s2", "Review PR")])
    assert not _map_file(monkeypatch).exists()

    await tick(post_new=False)

    channel.create_thread.assert_not_called()
    adapter.handle_message.assert_not_called()
    assert _seen(adapter) == {
        "s1": (None, "Fix the gate"), "s2": (None, "Review PR")}
    assert _map_file(monkeypatch).exists()


@pytest.mark.asyncio
async def test_new_session_opens_a_thread_with_a_starter_message_and_resumes(monkeypatch):
    adapter, tick, channel, _holder, _titles = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)  # fresh install: seed on an empty machine

    _holder["rows"] = [_row("s1", "Fix the gate")]  # a session started at the desk
    from plugins.platforms.discord import adapter_session_mirror as _m
    print("DBG channel:", _m.mirror_channel(adapter), "| admin:", _m.mirror_admin_id(adapter),
          "| extra:", dict(adapter.config.extra))
    await tick(post_new=True)

    channel.create_thread.assert_awaited_once()
    name = channel.create_thread.await_args.kwargs["name"]
    assert "Fix the gate" in name and name.startswith("[")  # the machine tag prefixes the title
    thread = channel.create_thread.return_value
    thread.send.assert_awaited_once()  # the starter message: an empty thread sits in Discord's
    assert "Mirrored session" in thread.send.await_args.args[0]  # hidden popup, not the channel
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.text == "/resume s1 --all"
    assert event.internal is False  # the IDOR gate stays real: no impersonation by internal bypass
    assert event.source.user_id == "admin-1"
    assert event.source.thread_id == "999"
    assert _seen(adapter) == {"s1": ("999", "Fix the gate")}
    persisted = json.loads(_map_file(monkeypatch).read_text())
    assert persisted == {"s1": ["999", "Fix the gate"]}  # survives restarts


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
    adapter, tick, channel, holder, _titles = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)  # seed on an empty machine

    holder["rows"] = [_row("s1", ""), _row("s2", "Fix the gate"), _row("s3", "Fix the gate", source="discord")]
    await tick(post_new=True)

    # s1 has no title, s3 is a Discord conversation: only s2 opens a thread.
    channel.create_thread.assert_awaited_once()
    assert _seen(adapter) == {"s2": ("999", "Fix the gate")}


@pytest.mark.asyncio
async def test_unsettled_session_is_not_bound_but_retried(monkeypatch):
    """A session with durable work (a live turn) is left alone this tick — the settled guard —
    and mirrored on a later tick once it settles."""
    adapter, tick, channel, holder, _titles = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)  # seed on an empty machine

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
    _adapter_obj, tick, channel, holder, _titles = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)  # seed on an empty machine
    holder["rows"] = [_row("s1", "Fix the gate")]
    channel.create_thread = AsyncMock(side_effect=RuntimeError("missing permissions"))

    await tick(post_new=True)
    await tick(post_new=True)
    channel.create_thread.assert_awaited_once()  # marked seen: no retry storm


@pytest.mark.asyncio
async def test_missing_group_admin_list_fails_closed(monkeypatch):
    """No group_allow_admin_from -> the mirror idles: cross-origin /resume is admin-only and a
    thread source resolves the GROUP list, so the DM-scope key does not count."""
    channel = _mirror_channel()
    adapter, tick, _seen_map, _holder, _titles = _wired_adapter(
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
    await adapter_session_mirror.tick(adapter, adapter_session_mirror._load_map(adapter), post_new=True)
    adapter.handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_machine_retitle_renames_the_thread_via_the_client_cache(monkeypatch):
    """A /title change on the machine renames the mirrored thread — resolved through the client
    cache / API (get_channel), never the per-channel thread list that reconnects reset."""
    adapter, tick, channel, holder, _titles = _wired_adapter(monkeypatch, [])
    thread = _thread_named("[cuong-page-mac] Fix the gate")
    channel.create_thread = AsyncMock(return_value=thread)
    await tick(post_new=False)  # seed on an empty machine

    holder["rows"] = [_row("s1", "Fix the gate")]
    await tick(post_new=True)  # mirrored: bound thread, map persisted

    holder["rows"] = [_row("s1", "Fix the gate (done)")]
    thread.edit = AsyncMock()
    adapter._client.get_channel = lambda cid: thread if str(cid) == "999" else (channel if str(cid) == "555" else None)
    await tick(post_new=True)

    thread.edit.assert_awaited_once()
    assert thread.edit.await_args.kwargs["name"] == "[cuong-page-mac] Fix the gate (done)"


@pytest.mark.asyncio
async def test_discord_thread_rename_renames_the_session_and_strips_the_tag(monkeypatch):
    """The reverse direction: renaming the thread in Discord retitles the session (Discord →
    machine) with the tag STRIPPED — the tag is machine-added and must never enter the title,
    or the poller would re-rename and the two directions would feed each other."""
    adapter, tick, channel, _holder, titles = _wired_adapter(monkeypatch, [])
    thread = _thread_named("[cuong-page-mac] Fix the gate")
    channel.create_thread = AsyncMock(return_value=thread)
    await tick(post_new=False)  # seed on an empty machine

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
async def test_bindings_survive_a_restart(monkeypatch):
    """The persisted map outlives the process: a fresh adapter (a restart) re-adopts the bound
    thread and both sync directions keep working from the restored map."""
    adapter, tick, channel, holder, _titles = _wired_adapter(monkeypatch, [])
    thread = _thread_named("[cuong-page-mac] Fix the gate")
    channel.create_thread = AsyncMock(return_value=thread)
    await tick(post_new=False)  # seed on an empty machine
    holder["rows"] = [_row("s1", "Fix the gate")]
    await tick(post_new=True)  # mirrored
    map_after = _seen(adapter)

    # the restart: a NEW adapter object (fresh in-memory cache), same persisted map
    adapter2, tick2, _channel2, _holder2, _titles2 = _wired_adapter(monkeypatch, [], channel=channel)
    adapter2._client.get_channel = lambda cid: thread if str(cid) == "999" else _channel2 if str(cid) == "555" else None
    assert _seen(adapter2) == map_after  # re-adopted from the map file

    _holder2["rows"] = [_row("s1", "Fix the gate (done)")]
    thread.edit = AsyncMock()
    await tick2(post_new=True)
    thread.edit.assert_awaited_once()  # the restart did not orphan the binding


@pytest.mark.asyncio
async def test_session_started_during_downtime_is_mirrored_after_restart(monkeypatch):
    """A session that appeared while the gateway was down is genuinely new once the map
    persisted: the next tick mirrors it (the fresh-install seed rule does not apply)."""
    adapter, tick, channel, holder, _titles = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)  # seed on an empty machine
    assert _seen(adapter) == {}

    holder["rows"] = [_row("s1", "Started while the gateway was down")]
    await tick(post_new=True)
    channel.create_thread.assert_awaited_once()
    assert _seen(adapter) == {"s1": ("999", "Started while the gateway was down")}


@pytest.mark.asyncio
async def test_bound_discord_source_session_title_still_syncs(monkeypatch):
    """Once /resume lands, the session row's source flips to discord — the local-sources roster
    query stops returning it. The drift sync must read bound sessions directly, or the machine
    /title would never reach the thread (the live failure this pins)."""
    _adapter_obj, tick, channel, holder, _titles = _wired_adapter(monkeypatch, [])
    thread = _thread_named("[cuong-page-mac] Fix the gate", send=AsyncMock())
    channel.create_thread = AsyncMock(return_value=thread)
    channel.get_thread = lambda tid: thread
    await tick(post_new=False)  # seed on an empty machine

    holder["rows"] = [_row("s1", "Fix the gate")]
    await tick(post_new=True)  # mirrored: bound in the persisted map

    # the lane move flipped the row source to discord, and /title changed the title
    thread.edit = AsyncMock()
    channel.threads = [thread]  # the mirror channel lists its bound thread
    holder["rows"] = [_row("s1", "Fix the gate (done)", source="discord")]
    await tick(post_new=True)

    thread.edit.assert_awaited_once()
    assert thread.edit.await_args.kwargs["name"] == "[cuong-page-mac] Fix the gate (done)"


@pytest.mark.asyncio
async def test_platform_thread_update_drives_the_discord_to_machine_sync(monkeypatch):
    """The adapter's own on_thread_update normalization (the ONE handler Discord drives) calls
    the mirror sync — a renamed mirror thread retitles its session."""
    adapter, tick, channel, holder, titles = _wired_adapter(monkeypatch, [])
    thread = _thread_named("[cuong-page-mac] Fix the gate", send=AsyncMock())
    channel.create_thread = AsyncMock(return_value=thread)
    channel.get_thread = lambda tid: thread
    await tick(post_new=False)
    holder["rows"] = [_row("s1", "Fix the gate")]
    await tick(post_new=True)

    after = SimpleNamespace(id=999, name="[cuong-page-mac] ship it")
    before = SimpleNamespace(id=999, name="[cuong-page-mac] Fix the gate")
    await adapter._on_platform_thread_update(before, after)

    assert titles == [("s1", "ship it")]


@pytest.mark.asyncio
async def test_backfill_threads_the_whole_roster_on_the_seeding_tick(monkeypatch):
    """`session_mirror_backfill`: old sessions (pre-mirror) get their threads on the first tick
    instead of being seeded quietly — the "pull in old sessions" ask."""
    adapter, tick, channel, _holder, _titles = _wired_adapter(
        monkeypatch, [_row("s1", "Old one"), _row("s2", "Older still"), _row("s3", "")],
        extra={"session_mirror_backfill": True})

    await tick(post_new=False)  # the seeding tick itself does the backfill

    assert channel.create_thread.await_count == 2  # s3 untitled: skipped
    seen = adapter_session_mirror._load_map(adapter)
    assert seen["s1"][0] is not None and seen["s2"][0] is not None and "s3" not in seen  # untitled skipped


@pytest.mark.asyncio
async def test_backfill_off_keeps_the_quiet_seed(monkeypatch):
    adapter, tick, channel, _holder, _titles = _wired_adapter(
        monkeypatch, [_row("s1", "Old one")])
    await tick(post_new=False)
    channel.create_thread.assert_not_called()  # default: quiet seed
    assert _seen_map(adapter)["s1"] == (None, "Old one")


def _seen_map(adapter):
    return adapter_session_mirror._load_map(adapter)


@pytest.mark.asyncio
async def test_backfilled_sessions_are_not_recreated_on_later_ticks(monkeypatch):
    _adapter_obj, tick, channel, _holder, _titles = _wired_adapter(
        monkeypatch, [_row("s1", "Old one")], extra={"session_mirror_backfill": True})
    await tick(post_new=False)
    channel.create_thread.assert_awaited_once()

    await tick(post_new=True)
    await tick(post_new=False)
    channel.create_thread.assert_awaited_once()  # bound: no duplicates on later ticks
