"""Exhausted fallback chain: one terminal chat line, kept by the Telegram noise filter."""

from types import SimpleNamespace

import pytest

from agent.chat_completion_helpers import (
    _fallback_chain_exhausted,
    _fallback_exhausted_notice,
)
from gateway.run import _prepare_gateway_status_message

SWITCH_LINE = "⚠️ Model fallback: gpt-5.6 via openai unavailable (rate limit); using qwen3.8-max via alibaba."
EXHAUSTED_LINE = _fallback_exhausted_notice(
    "gpt-5.6", "openai", "qwen3.8-max", "alibaba"
)


def _agent(chain):
    buffered: list[str] = []
    return SimpleNamespace(
        _fallback_chain=chain,
        _fallback_index=len(chain),
        _rate_limited_until=0,
        model="gpt-5.6",
        provider="openai",
        _primary_runtime={"model": "gpt-5.6", "provider": "openai"},
        _buffered=buffered,
        _buffer_diagnostic_status=buffered.append,
    )


@pytest.mark.parametrize("line", [SWITCH_LINE, EXHAUSTED_LINE])
def test_telegram_filter_keeps_switch_and_exhausted_lines(line):
    assert _prepare_gateway_status_message("telegram", "status", line) is not None


def test_exhausted_chain_buffers_one_terminal_line():
    agent = _agent([{"provider": "alibaba", "model": "qwen3.8-max"}])

    assert _fallback_chain_exhausted(agent, None) is False

    assert agent._buffered == [EXHAUSTED_LINE]
    assert EXHAUSTED_LINE.startswith(
        "Both models failed: gpt-5.6 via openai, then qwen3.8-max via alibaba."
    )
    assert EXHAUSTED_LINE.endswith("This turn stopped.")
    lowered = EXHAUSTED_LINE.lower()
    for banned in ("retrying", "rate limited", "max retries", "auxiliary"):
        assert banned not in lowered


def test_empty_chain_buffers_nothing():
    agent = _agent([])

    assert _fallback_chain_exhausted(agent, None) is False

    assert agent._buffered == []
