"""`/sessions all` for an admin crosses the platform filter.

The whole point of ``--all`` is reaching machine-local (TUI/CLI) sessions from a messaging chat (the
session mirror binds a local session with ``/resume <id> --all``): a platform-scoped query hid them.
This was a test of tests/gateway/test_discord_session_mirror.py; the mirror moved to the
`lane-mirror` plugin, the listing change stayed in core, so its test stayed too.
"""

from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.slash_commands_session import GatewaySessionCommandsMixin
from plugins.platforms.discord.adapter import DiscordAdapter


@pytest.mark.asyncio
async def test_sessions_all_for_admin_lists_local_sessions(monkeypatch):
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="fake-token", extra={}))
    adapter._list_titled_sessions = GatewaySessionCommandsMixin._list_titled_sessions.__get__(adapter)
    captured = {}

    async def fake_list(source=None, session_key=None, limit=10, order_by_last_active=True):
        captured["source"] = source
        return [{"id": "local-1", "title": "TUI session", "source": "local"}]

    adapter._session_db = SimpleNamespace(list_sessions_rich=fake_list)
    monkeypatch.setattr(type(adapter), "_resume_caller_is_admin", lambda self, source: True, raising=False)

    async def _always_visible(self, source, row, allow_all):
        return True
    monkeypatch.setattr(type(adapter), "_resume_row_visible", _always_visible, raising=False)
    source = SimpleNamespace(platform=Platform.DISCORD, user_id="admin-1")

    rows = await adapter._list_titled_sessions(source, "agent:main:discord:dm:555", allow_all=True)

    assert captured["source"] is None  # widen drops the platform filter
    assert rows and rows[0]["title"] == "TUI session"
