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


# ── /cwd dynamic autocomplete registration ────────────────────────────────────


def test_cwd_native_spec_uses_the_autocomplete_registration():
    """The spec entry must be template-None (routed to _register_cwd_slash before the
    already_registered snapshot) — a /cwd {path} template here would make _auto_register add a
    second plain /cwd and Discord's bulk sync would carry the duplicate."""
    from plugins.platforms.discord.adapter import _native_slash_commands
    (spec,) = [s for s in _native_slash_commands() if s[0] == "cwd"]
    assert spec[3] is None and spec[2] == ()


def test_register_cwd_slash_adds_the_command(monkeypatch):
    """The template-None spec routes /cwd to _register_cwd_slash, which registers exactly one
    command named cwd on the tree — the pre-snapshot slot, so _auto_register never duplicates it."""
    from types import SimpleNamespace
    adapter = _adapter()
    registered: dict = {}
    tree = SimpleNamespace(
        command=lambda name, description: (lambda fn: registered.update(name=name, fn=fn)),
        add_command=lambda cmd: registered.setdefault("added", cmd),
    )
    adapter._register_cwd_slash(tree, "Show or set workspace")
    assert registered["name"] == "cwd" and callable(registered["fn"])


def test_autocomplete_choices_filter_cap_and_cache(monkeypatch):
    adapter = _adapter()
    many = [f"/w/d{n}" for n in range(40)]
    calls = []

    def _counting(db=None):
        calls.append(1)
        return many
    monkeypatch.setattr("gateway.workspace_candidates.candidate_workspaces", _counting)
    monkeypatch.delattr(adapter, "_cwd_candidates_cache", raising=False)

    assert len(adapter._cwd_autocomplete_choices("")) == 25  # broad query hits the cap
    d3 = adapter._cwd_autocomplete_choices("d3")
    assert len(d3) == 11 and all("d3" in c.name for c in d3)  # d3 + d30..d39
    assert adapter._cwd_autocomplete_choices("zzz-none") == []
    assert len(calls) == 1  # TTL cache: one build serves all three keystrokes
