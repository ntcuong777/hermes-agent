"""Cursor SDK usage accounting (agent/cursor_usage.py): a response that cannot adjudicate
context pressure must say so to the compressor, or preflight compression defers forever
and a cursor_sdk session never auto-compacts (the codex app-server runtime already does
this at agent/codex_runtime.py — same bypass-of-the-generic-loop shape)."""

import pytest

from agent.cursor_usage import record_usage


class FakeCompressor:
    def __init__(self, awaiting=False):
        self.awaiting_real_usage_after_compression = awaiting
        self.updates = []
        self.noted_usage_less = False

    def update_from_response(self, usage):
        self.updates.append(usage)
        # Mirror the real latch consumption so the codex-precedent call is observable.
        self.awaiting_real_usage_after_compression = False

    def note_usage_less_response(self):
        self.noted_usage_less = True


class FakeAgent:
    def __init__(self, compressor):
        self.model = "grok-4.7"
        self.provider = "cursor"
        self.base_url = "https://api.cursor.com"
        self.session_api_calls = 0
        self._session_db = None
        self.session_id = "s1"
        self.context_compressor = compressor


# Consistent, separate-bucket usage: input+output+cacheRead+cacheWrite == total.
GOOD_USAGE = {
    "inputTokens": 100,
    "outputTokens": 20,
    "cacheReadTokens": 30,
    "cacheWriteTokens": 50,
    "totalTokens": 200,
}

# Nothing reconciles: books only the certain figures, which carry no prompt_tokens.
INCONSISTENT_USAGE = {
    "inputTokens": 10,
    "outputTokens": 20,
    "cacheReadTokens": 30,
    "cacheWriteTokens": 40,
    "totalTokens": 999,
}


def test_consistent_usage_updates_the_compressor_and_never_notes_usage_less():
    compressor = FakeCompressor()
    agent = FakeAgent(compressor)
    record_usage(agent, GOOD_USAGE)
    assert compressor.noted_usage_less is False
    assert compressor.updates and "prompt_tokens" in compressor.updates[0]
    assert compressor.updates[0]["prompt_tokens"] == 180  # 100 uncached + 30 + 50


def test_missing_usage_notes_usage_less_and_unlatches_awaiting():
    compressor = FakeCompressor(awaiting=True)
    agent = FakeAgent(compressor)
    record_usage(agent, None)
    assert compressor.noted_usage_less is True
    # The codex precedent: no usage cannot adjudicate the pending compaction, so the
    # one-response deferral latch is consumed instead of holding preflight forever.
    assert compressor.updates == [{}]
    assert compressor.awaiting_real_usage_after_compression is False


def test_inconsistent_usage_notes_usage_less_too():
    """A report that does not reconcile against its own total books no prompt tokens, so the
    compressor would stay blind forever if it were not told this response cannot adjudicate."""
    compressor = FakeCompressor()
    agent = FakeAgent(compressor)
    record_usage(agent, INCONSISTENT_USAGE)
    assert compressor.noted_usage_less is True
    assert compressor.updates == []


def test_usage_less_turn_unlocks_preflight_compression_on_a_real_compressor():
    """The symptom end to end at the deciding unit: before the fix a fresh cursor session's
    first over-threshold rough estimate defers (no real usage, provider not proven to omit
    it), and every usage-less turn leaves that state unchanged — so preflight compression
    never fires and the session runs past its window. After one usage-less turn the
    provider is proven usage-less and the rough estimate decides."""
    from unittest.mock import patch

    from agent.context_compressor import ContextCompressor

    with patch(
        "agent.context_compressor.get_model_context_length", return_value=256_000
    ):
        compressor = ContextCompressor(
            model="grok-4.7", threshold_percent=0.50, quiet_mode=True
        )
        _ = compressor.context_length
    agent = FakeAgent(compressor)
    over_window = 300_000
    assert compressor.should_defer_preflight_to_real_usage(over_window) is True
    record_usage(agent, None)
    assert compressor.should_defer_preflight_to_real_usage(over_window) is False
