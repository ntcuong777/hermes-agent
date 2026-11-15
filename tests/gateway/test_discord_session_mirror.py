"""The Discord session mirror: new machine-local sessions (CLI/TUI — the `local` origin; api
sessions are NOT mirrored) automatically get a thread in the mirror channel, bound to the session
by the same `/resume <id> --all` pipeline a typed command uses. The synthetic turn is a normal
(non-internal) event acting as the configured `group_allow_admin_from` admin — a thread source
resolves the group scope, so the DM-scope key does not count, and the mirror fails closed without
it. First tick after a (re)start only seeds; a session with unsettled durable work (a live turn)
is retried on later ticks instead of being bound mid-turn.
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


def _channel():
    channel = MagicMock()
    channel.id = 555
    channel.name = "inbox"
    channel.guild.id = 1
    channel.create_thread = AsyncMock(return_value=SimpleNamespace(id=999, name="t"))
    return channel


def _wired_adapter(monkeypatch, rows, *, channel=None, extra=None):
    """Adapter whose mirror reads `rows` from a stubbed session db honouring the `sources`
    filter, against a REAL _mirror resolution (config extra + client cache). `rows` may be
    replaced between ticks via `holder["rows"]`."""
    base = {"session_mirror_channel": "555", "group_allow_admin_from": ["admin-1"]}
    base.update(extra or {})
    adapter = _adapter(base)
    seen = set()
    holder = {"rows": list(rows)}

    async def tick(post_new):
        await adapter_session_mirror.tick(adapter, seen, post_new=post_new)

    def db(**kw):
        sources = kw.get("sources")
        return [r for r in holder["rows"] if not sources or r.get("source", "local") in sources]

    store = SimpleNamespace(_db_for_key=lambda key: SimpleNamespace(list_sessions_rich=db))
    adapter.gateway_runner = SimpleNamespace(
        session_store=store, _session_key_for_source=lambda source: "agent:main:discord:dm:555",
        _profile_name_for_source=lambda source, adapter_profile=None: "main")
    adapter.handle_message = AsyncMock()
    if channel is None:
        channel = _channel()
    adapter._client = MagicMock()
    adapter._client.get_channel = lambda cid: channel if str(cid) == "555" else None
    return adapter, tick, seen, channel, holder


@pytest.mark.asyncio
async def test_seeding_tick_creates_no_threads_and_fills_seen(monkeypatch):
    adapter, tick, seen, channel, _holder = _wired_adapter(
        monkeypatch, [_row("s1", "Fix the gate"), _row("s2", "Review PR")])

    await tick(post_new=False)

    channel.create_thread.assert_not_called()
    adapter.handle_message.assert_not_called()
    assert seen == {"s1", "s2"}


@pytest.mark.asyncio
async def test_new_session_opens_a_thread_and_resumes_into_it(monkeypatch):
    adapter, tick, seen, channel, holder = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)  # seed on an empty machine

    holder["rows"] = [_row("s1", "Fix the gate")]  # a session started at the desk
    await tick(post_new=True)

    channel.create_thread.assert_awaited_once()
    assert "Fix the gate" in channel.create_thread.await_args.kwargs["name"]
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.text == "/resume s1 --all"
    assert event.internal is False  # the IDOR gate stays real: no impersonation by internal bypass
    assert event.source.user_id == "admin-1"
    assert event.source.thread_id == "999"
    assert seen == {"s1"}


@pytest.mark.asyncio
async def test_state_only_changes_and_removals_post_nothing(monkeypatch):
    adapter, tick, _seen, channel, _holder = _wired_adapter(monkeypatch, [_row("s1", "Fix the gate")])
    await tick(post_new=False)

    await tick(post_new=True)  # same session: no post
    channel.create_thread.assert_not_called()
    adapter.handle_message.assert_not_called()

    await tick(post_new=True)  # session gone: no post either (removals are not news)
    channel.create_thread.assert_not_called()


@pytest.mark.asyncio
async def test_untitled_discord_sourced_rows_are_skipped(monkeypatch):
    """Untitled rows never mirror; Discord conversations are excluded by the sources filter."""
    _adapter_obj, tick, seen, channel, holder = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)  # seed on an empty machine

    holder["rows"] = [_row("s1", ""), _row("s2", "Fix the gate"), _row("s3", "Fix the gate", source="discord")]
    await tick(post_new=True)

    # s1 has no title, s3 is a Discord conversation: only s2 opens a thread.
    channel.create_thread.assert_awaited_once()
    assert seen == {"s2"}


@pytest.mark.asyncio
async def test_unsettled_session_is_not_bound_but_retried(monkeypatch):
    """A session with durable work (a live turn) is left alone this tick — the settled guard —
    and mirrored on a later tick once it settles."""
    adapter, tick, seen, channel, holder = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)

    holder["rows"] = [_row("s1", "Fix the gate")]

    async def refusing(db, sid):
        raise RuntimeError("wait for the current turn to finish, then retry the handoff")
    monkeypatch.setattr(adapter_session_mirror, "check_settled", refusing)
    await tick(post_new=True)
    channel.create_thread.assert_not_called()  # not bound mid-turn
    adapter.handle_message.assert_not_called()
    assert seen == set()  # retried later

    monkeypatch.setattr(adapter_session_mirror, "check_settled", AsyncMock())
    await tick(post_new=True)
    channel.create_thread.assert_awaited_once()  # settles -> mirrored
    assert seen == {"s1"}


@pytest.mark.asyncio
async def test_thread_creation_failure_marks_seen_no_retry_storm(monkeypatch):
    _adapter_obj, tick, _seen, channel, holder = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)
    holder["rows"] = [_row("s1", "Fix the gate")]
    channel.create_thread = AsyncMock(side_effect=RuntimeError("missing permissions"))

    await tick(post_new=True)
    await tick(post_new=True)
    channel.create_thread.assert_awaited_once()  # marked seen: no retry storm


@pytest.mark.asyncio
async def test_missing_group_admin_list_fails_closed(monkeypatch):
    """No group_allow_admin_from -> the mirror idles: cross-origin /resume is admin-only and a
    thread source resolves the GROUP list, so the DM-scope key does not count."""
    channel = _channel()
    adapter, tick, _seen, _ch, _holder = _wired_adapter(
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
    seen: set = set()
    await adapter_session_mirror.tick(adapter, seen, post_new=True)
    adapter.handle_message.assert_not_called()
