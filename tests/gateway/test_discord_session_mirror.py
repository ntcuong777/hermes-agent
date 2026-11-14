"""The Discord session mirror: new machine-local sessions (CLI/TUI/api — anything not a Discord
conversation) automatically get a thread in the mirror channel, bound to the session by the same
`/resume <id> --all` pipeline a typed command uses. The synthetic turn carries an explicitly
configured admin id — cross-origin resume is admin-only by IDOR design, and the mirror must not
bypass that gate. First tick after a (re)start only seeds: existing sessions are not news.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig

from plugins.platforms.discord.adapter import DiscordAdapter


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


def _wired_adapter(monkeypatch, rows, *, channel=None, admin="admin-1"):
    """Adapter whose mirror reads `rows` from a stubbed session db (honouring the `sources`
    filter like the real one); captures dispatches. `rows` may be replaced between ticks via
    `holder["rows"]`."""
    adapter = _adapter({"session_mirror_channel": "555"})
    seen = set()
    holder = {"rows": list(rows)}

    async def tick(post_new):
        await adapter._session_mirror_tick(seen, post_new=post_new)

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
    monkeypatch.setattr(adapter, "_mirror_config", lambda: (channel, admin))
    return adapter, tick, seen, channel, holder


@pytest.mark.asyncio
async def test_seeding_tick_creates_no_threads_and_fills_seen(monkeypatch):
    rows = [_row("s1", "Fix the gate"), _row("s2", "Review PR")]
    adapter, tick, seen, channel, _holder = _wired_adapter(monkeypatch, rows)

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
    assert event.internal is True
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
async def test_untitled_and_empty_rows_are_skipped(monkeypatch):
    _adapter_obj, tick, seen, channel, holder = _wired_adapter(monkeypatch, [])
    await tick(post_new=False)  # seed on an empty machine

    holder["rows"] = [_row("s1", ""), _row("s2", "Fix the gate"), _row("s3", "Fix the gate", source="discord")]
    await tick(post_new=True)

    # s1 has no title, s3 is a Discord conversation: only s2 opens a thread.
    channel.create_thread.assert_awaited_once()
    assert seen == {"s2"}


@pytest.mark.asyncio
async def test_mirror_without_an_admin_id_stays_idle(monkeypatch):
    adapter, tick, _seen, channel, _holder = _wired_adapter(monkeypatch, [_row("s1", "Fix the gate")], admin=None)
    await tick(post_new=False)
    await tick(post_new=True)
    channel.create_thread.assert_not_called()
    adapter.handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_no_mirror_channel_configured_stays_idle(monkeypatch):
    adapter = _adapter({})
    adapter.gateway_runner = SimpleNamespace(session_store=None)
    adapter.handle_message = AsyncMock()
    # No channel resolvable: _mirror_config returns (None, ...) from the raw extra.
    seen: set = set()
    await adapter._session_mirror_tick(seen, post_new=True)
    adapter.handle_message.assert_not_called()
