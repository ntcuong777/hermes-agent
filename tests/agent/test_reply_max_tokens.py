"""Pin the P46 reply cap: `agent.reply_max_tokens` clamps the per-call output cap.

Without a configured cap the OpenAI-compatible routes send no ``max_tokens`` at all and
Anthropic sends the model catalog's max (harness-leaderboard: Pi caps replies at 16384 and
is the board's cheapest harness per call; Hermes leaves the cap off). The clamp is applied
where each request builder resolves ``agent.max_tokens``; the one-shot ephemeral cap (the
truncation-retry ladder) is consumed before the clamp, so a boosted retry still wins.
"""

from agent.chat_completion_helpers import _reply_capped_max_tokens


class _Agent:
    def __init__(self, max_tokens=None, reply_max_tokens=None):
        self.max_tokens = max_tokens
        self.reply_max_tokens = reply_max_tokens


def test_off_by_default_the_catalog_max_stays():
    assert _reply_capped_max_tokens(_Agent(65536, None), 65536) == 65536
    assert _reply_capped_max_tokens(_Agent(None, None), None) is None


def test_cap_lower_than_the_models_own_clamps():
    assert _reply_capped_max_tokens(_Agent(65536, 16384), 65536) == 16384


def test_cap_never_raises_above_the_models_own():
    assert _reply_capped_max_tokens(_Agent(8192, 16384), 8192) == 8192


def test_cap_with_unknown_catalog_max_sets_the_cap():
    # agent.max_tokens None means "no known limit": builders pass None and the Anthropic
    # adapter falls back to the catalog — the clamp must put the cap on the wire instead.
    assert _reply_capped_max_tokens(_Agent(None, 16384), None) == 16384


def test_junk_values_are_off():
    assert _reply_capped_max_tokens(_Agent(65536, "16k"), 65536) == 65536
    assert _reply_capped_max_tokens(_Agent(65536, 0), 65536) == 65536


def test_session_state_default_is_off_and_config_read_applies():
    # _SESSION_STATE carries the default; _init_session_state overrides it from
    # config `agent.reply_max_tokens`. The default stays None so a bad config is off.
    from agent.agent_init import _SESSION_STATE, _set_defaults

    agent = type("A", (), {})()
    _set_defaults(agent, _SESSION_STATE)
    assert agent.reply_max_tokens is None


def test_builders_clamp_anthropic_kwargs():
    """The Anthropic builder sends the clamped cap when no ephemeral boost is pending."""
    from agent.chat_completion_helpers import _build_anthropic_kwargs

    sent = {}

    class _Transport:
        def build_kwargs(self, **kwargs):
            sent.update(kwargs)
            return {"max_tokens": kwargs["max_tokens"]}

    class _CappedAgent(_Agent):
        model = "claude-sonnet-5-5"
        _is_anthropic_oauth = False
        _anthropic_preserve_dots = lambda self: False
        _anthropic_base_url = None
        _oauth_1m_beta_disabled = False
        context_compressor = None

        def _get_transport(self):
            return _Transport()

        def _prepare_anthropic_messages_for_api(self, messages):
            return messages

    agent = _CappedAgent(65536, 16384)
    _build_anthropic_kwargs(agent, [], None, None, {})
    assert sent["max_tokens"] == 16384

    agent.reply_max_tokens = None
    _build_anthropic_kwargs(agent, [], None, None, {})
    assert sent["max_tokens"] == 65536
