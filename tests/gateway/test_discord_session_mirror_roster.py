"""The session mirror's roster: interactive desk sessions only — the TUI/CLI source labels and
`local` rows a TUI/CLI created — never tool/cron/subagent/oneshot runs or messaging conversations;
an old session newly visible to the roster is seeded, not threaded.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig

from plugins.platforms.discord.adapter import DiscordAdapter
from plugins.platforms.discord import adapter_session_mirror as mirror


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


# ── roster filter ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_roster_mirrors_tui_and_cli_and_skips_runs_and_platforms():
    """The live failure: TUI sessions are stored with source `tui`, so a `local`-only roster never
    saw them. tui/cli (and a TUI/CLI-created `local` row) mirror; runs and chats never do."""
    rows = [_row("tui-1", "tui"), _row("cli-1", "cli"), _row("loc-1", "local", "tui"),
            _row("loc-tool", "local", "tool"), _row("tool-1", "tool"), _row("cron-1", "cron"),
            _row("sub-1", "subagent"), _row("one-1", "oneshot"), _row("dc-1", "discord"),
            _row("tg-1", "telegram")]
    adapter = _adapter()
    thread = _thread()
    _wire_roster(adapter, _filtering_db(rows), _channel(thread))
    mirror._save_map(adapter, {})  # an existing install: new sessions are news

    await mirror.tick(adapter, mirror._load_map(adapter), post_new=True)

    assert sorted(mirror._load_map(adapter)) == ["cli-1", "loc-1", "tui-1"]
    resumed = sorted(c.args[0].text for c in adapter.handle_message.await_args_list)
    assert resumed == ["/resume cli-1 --all", "/resume loc-1 --all", "/resume tui-1 --all"]


@pytest.mark.asyncio
async def test_old_sessions_newly_in_the_roster_are_seeded_not_threaded():
    """Widening the roster must not mass-thread (and lane-move) every old desk session: only
    recently active unseen sessions are news."""
    old = time.time() - 3 * 24 * 3600
    rows = [_row("old-1", "tui", last_active=old), _row("new-1", "tui", last_active=time.time())]
    adapter = _adapter()
    thread = _thread()
    channel = _channel(thread)
    _wire_roster(adapter, _filtering_db(rows), channel)
    mirror._save_map(adapter, {})

    await mirror.tick(adapter, mirror._load_map(adapter), post_new=True)

    channel.create_thread.assert_awaited_once()
    assert mirror._load_map(adapter) == {"old-1": (None, "title old-1"), "new-1": ("999", "title new-1")}
