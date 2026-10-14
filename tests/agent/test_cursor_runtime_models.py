"""Cursor SDK runtime: model/effort selection, safe errors, and usage accounting that never invents zeros."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

import agent.cursor_runtime as cr
import run_agent
from tests.agent.test_cursor_runtime import FakeAgent, _worker


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")


def _turn(agent, monkeypatch, command):
    monkeypatch.setattr(cr, "_worker_command", lambda: command)
    monkeypatch.setattr(cr, "stage_tool_call_message",
                        lambda agent, assistant_message, finish_reason, messages:
                        (agent._build_assistant_message(assistant_message, finish_reason), False))
    return cr.run_cursor_sdk_turn(agent, user_message="go", original_user_message="go",
                                  messages=[{"role": "user", "content": "go"}], effective_task_id="t")


def test_an_explicit_effort_is_sent_and_an_unset_one_is_absent(tmp_path, monkeypatch):
    seen = tmp_path / "seen.json"
    command = _worker(tmp_path, f"""
        open({str(seen)!r}, "w").write(json.dumps(start))
        send({{"type": "done", "turnId": tid, "status": "finished"}})
    """)
    agent = FakeAgent()
    agent.reasoning_config = {"enabled": True, "effort": "High"}
    _turn(agent, monkeypatch, command)
    assert json.loads(seen.read_text())["effort"] == "high"
    agent.reasoning_config = None
    _turn(agent, monkeypatch, command)
    assert "effort" not in json.loads(seen.read_text())
    agent.reasoning_config = {"enabled": False}
    _turn(agent, monkeypatch, command)
    assert json.loads(seen.read_text())["effort"] == "none"


@pytest.mark.parametrize("code,needle", [
    ("unknown-model", "catalog"),
    ("unsupported-effort", "reasoning effort"),
    ("catalog-unavailable", "catalog"),
    ("native-tool", "native"),
])
def test_worker_failure_codes_become_plain_safe_messages(tmp_path, monkeypatch, code, needle):
    command = _worker(tmp_path, f'send({{"type": "failed", "turnId": tid, "code": "{code}"}})')
    result = _turn(FakeAgent(), monkeypatch, command)
    assert result["completed"] is False
    assert needle in result["error"].lower()
    assert "test-key-not-real" not in json.dumps(result, default=str)


def test_a_missing_key_never_reaches_the_worker(tmp_path, monkeypatch):
    monkeypatch.delenv("CURSOR_API_KEY")
    result = _turn(FakeAgent(), monkeypatch, [sys.executable, "-c", "raise SystemExit(9)"])
    assert "CURSOR_API_KEY" in result["error"]


def _real_agent():
    return run_agent.AIAgent(
        api_key="stub", base_url="https://stub.invalid", provider="cursor", model="composer-2.5",
        api_mode="cursor_sdk", quiet_mode=True, skip_context_files=True, skip_memory=True,
        enabled_toolsets=["file"],
    )


def _done_with(tmp_path, usage):
    body = textwrap.dedent(f"""
        send({{"type": "event", "turnId": tid, "kind": "text", "text": "ok"}})
        send({{"type": "done", "turnId": tid, "status": "finished"{', "usage": ' + json.dumps(usage) if usage else ''}}})
    """)
    return _worker(tmp_path, body)


def _run(agent, monkeypatch, command):
    monkeypatch.setattr(cr, "_worker_command", lambda: command)
    return agent.run_conversation("go")


@pytest.mark.parametrize("usage", [
    # separate buckets: input + output + cache == total
    {"inputTokens": 10, "outputTokens": 5, "cacheReadTokens": 2, "cacheWriteTokens": 0, "totalTokens": 17},
    # input already includes the cache read: input + output == total
    {"inputTokens": 12, "outputTokens": 5, "cacheReadTokens": 2, "cacheWriteTokens": 0, "totalTokens": 17},
])
def test_reported_usage_is_booked_once_whichever_way_cache_is_counted(tmp_path, monkeypatch, usage):
    agent = _real_agent()
    before = (agent.session_api_calls, agent.session_prompt_tokens, agent.session_completion_tokens,
              agent.session_total_tokens)
    result = _run(agent, monkeypatch, _done_with(tmp_path, usage))
    assert result["completed"] is True
    assert agent.session_api_calls == before[0] + 1
    assert agent.session_prompt_tokens == before[1] + 12
    assert agent.session_completion_tokens == before[2] + 5
    assert agent.session_total_tokens == before[3] + 17
    assert agent.session_cache_read_tokens >= 2


def test_missing_usage_is_unknown_not_zero(tmp_path, monkeypatch):
    agent = _real_agent()
    before = (agent.session_prompt_tokens, agent.session_completion_tokens, agent.session_total_tokens)
    result = _run(agent, monkeypatch, _done_with(tmp_path, None))
    assert result["completed"] is True
    assert agent.session_api_calls == 1  # the call happened; its size is unknown
    assert (agent.session_prompt_tokens, agent.session_completion_tokens, agent.session_total_tokens) == before
    assert "total_tokens" not in result and "prompt_tokens" not in result


def test_inconsistent_usage_books_only_what_is_certain(tmp_path, monkeypatch):
    agent = _real_agent()
    odd = {"inputTokens": 3, "outputTokens": 4, "cacheReadTokens": 1, "cacheWriteTokens": 1, "totalTokens": 50}
    result = _run(agent, monkeypatch, _done_with(tmp_path, odd))
    assert result["completed"] is True
    assert agent.session_total_tokens == 50 and agent.session_completion_tokens == 4
