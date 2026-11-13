"""``platforms.discord.extra.thread_workspaces``: a channel/thread ID → host directory mapping that
a NEW session in that chat starts in. The adapter resolves an event's workspace with parent
inheritance (a thread under a mapped channel inherits), mirroring ``_resolve_channel_skills``.
The mapping lives in platform extra (like ``channel_skill_bindings``), so each profile resolves
its own under multiplex.
"""

import pytest

from gateway.config import PlatformConfig

from plugins.platforms.discord.adapter import DiscordAdapter


def _adapter(extra=None):
    return DiscordAdapter(PlatformConfig(enabled=True, token="fake-token", extra=extra or {}))


def test_normalizes_keys_and_drops_blank_paths():
    """YAML numeric ids (Discord channel ids are numbers in YAML) must match string lookups."""
    adapter = _adapter({"thread_workspaces": {
        12345: "~/code/foo",
        "67890": "   ",  # blank path: dropped
        "ok": "/tmp/w",
    }})
    assert adapter._resolve_channel_workspace("12345") == "~/code/foo"
    assert adapter._resolve_channel_workspace("67890") is None
    assert adapter._resolve_channel_workspace("ok") == "/tmp/w"


def test_matches_the_event_channel_and_inherits_the_parent():
    adapter = _adapter({"thread_workspaces": {
        "111": "~/code/site",    # parent channel: threads under it inherit
        "222": "~/code/papers",  # exact thread
    }})
    assert adapter._resolve_channel_workspace("222") == "~/code/papers"
    assert adapter._resolve_channel_workspace("333", "111") == "~/code/site"  # thread -> parent
    assert adapter._resolve_channel_workspace("444", "555") is None


def test_malformed_or_missing_mapping_resolves_to_none():
    assert _adapter()._resolve_channel_workspace("111") is None
    assert _adapter({"thread_workspaces": ["not", "a", "map"]})._resolve_channel_workspace("111") is None
