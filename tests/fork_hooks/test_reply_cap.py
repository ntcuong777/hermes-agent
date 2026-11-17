"""``resolve_reply_max_tokens``: plugins cap the per-call output tokens at the request builders (spec 04)."""

from __future__ import annotations

import pytest

from agent import reply_cap


class _Agent:
    model = "claude-sonnet-5-5"
    provider = "anthropic"
    session_id = "s1"
    platform = "cli"
    _is_anthropic_oauth = False
    _anthropic_base_url = None
    _oauth_1m_beta_disabled = False
    context_compressor = None

    def __init__(self, max_tokens=None):
        self.max_tokens = max_tokens
        self.sent = {}

    def _anthropic_preserve_dots(self):
        return False

    def _get_transport(self):
        agent = self

        class _Transport:
            def build_kwargs(self, **kwargs):
                agent.sent = kwargs
                return {"max_tokens": kwargs["max_tokens"]}
        return _Transport()

    def _prepare_anthropic_messages_for_api(self, messages):
        return messages


def test_without_a_callback_the_base_is_sent(manager):
    assert reply_cap.capped(_Agent(), 65536) == 65536
    assert reply_cap.capped(_Agent(), None) is None


def test_a_cap_lowers_and_never_raises(register):
    register(reply_cap.HOOK, lambda **_: 16384)
    assert reply_cap.capped(_Agent(), 65536) == 16384
    assert reply_cap.capped(_Agent(), 8192) == 8192
    assert reply_cap.capped(_Agent(), None) == 16384


def test_the_smallest_valid_cap_wins(register):
    register(reply_cap.HOOK, lambda **_: 32000)
    register(reply_cap.HOOK, lambda **_: 16384)
    register(reply_cap.HOOK, lambda **_: "4096")
    register(reply_cap.HOOK, lambda **_: True)
    register(reply_cap.HOOK, lambda **_: 0)
    assert reply_cap.capped(_Agent(), 65536) == 16384


def test_a_raising_callback_is_skipped(register):
    def boom(**_):
        raise RuntimeError("x")
    register(reply_cap.HOOK, boom)
    assert reply_cap.capped(_Agent(), 65536) == 65536


def test_the_callback_sees_the_documented_fields(register):
    seen = []
    register(reply_cap.HOOK, lambda **kw: seen.append(kw) and None)
    reply_cap.capped(_Agent(), 65536)
    assert {"base": 65536, "model": "claude-sonnet-5-5", "provider": "anthropic", "session_id": "s1",
            "platform": "cli"}.items() <= seen[0].items()


def test_the_anthropic_builder_sends_the_capped_value(register):
    from agent.chat_completion_helpers import _build_anthropic_kwargs

    register(reply_cap.HOOK, lambda **_: 16384)
    agent = _Agent(65536)
    _build_anthropic_kwargs(agent, [], None, None, {})
    assert agent.sent["max_tokens"] == 16384


def test_an_ephemeral_retry_boost_still_wins(register):
    from agent.chat_completion_helpers import _build_anthropic_kwargs

    register(reply_cap.HOOK, lambda **_: 16384)
    agent = _Agent(65536)
    agent._ephemeral_max_output_tokens = 64000
    _build_anthropic_kwargs(agent, [], None, None, {})
    assert agent.sent["max_tokens"] == 64000


def test_the_bedrock_builder_sends_the_capped_value(register):
    from agent.chat_completion_helpers import _build_bedrock_kwargs

    register(reply_cap.HOOK, lambda **_: 16384)
    agent = _Agent(65536)
    _build_bedrock_kwargs(agent, [], None, None)
    assert agent.sent["max_tokens"] == 16384
