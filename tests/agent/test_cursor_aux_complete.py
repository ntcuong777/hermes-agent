"""Cursor has no chat-completions route: an auxiliary call (title, summary, ...) for provider ``cursor`` is answered
by the profile's ``auxiliary_complete`` on the SDK worker, through a seam in ``agent.auxiliary_client``."""

from types import SimpleNamespace

import pytest

from agent import auxiliary_client as ac
from providers import get_provider_profile


def _profile():
    return get_provider_profile("cursor")


def _module():
    import sys

    return sys.modules[type(_profile()).__module__]


def test_flatten_puts_system_first_and_labels_the_rest():
    out = _module().flatten_messages(
        [
            {"role": "user", "content": "first"},
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": [{"type": "text", "text": "second"}, {"type": "image_url", "image_url": {}}]},
            {"role": "assistant", "content": "  "},
        ]
    )
    assert out == "be brief\n\nUSER: first\n\nUSER: second"


def test_the_profile_answers_on_the_worker_in_chat_completion_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "agent.cursor_summary_turn.run_summary_turn",
        lambda prompt, *, model, timeout=None: calls.append((prompt, model, timeout)) or "A Title",
    )
    resp = _profile().auxiliary_complete(
        {"model": "composer-2.5-fast", "messages": [{"role": "user", "content": "name this"}], "timeout": 12}
    )
    assert calls == [("USER: name this", "composer-2.5-fast", 12.0)]
    assert resp.choices[0].message.content == "A Title"
    assert resp.choices[0].finish_reason == "stop"


def test_a_missing_or_bad_timeout_is_left_to_the_worker_default(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "agent.cursor_summary_turn.run_summary_turn",
        lambda prompt, *, model, timeout=None: seen.append(timeout) or "x",
    )
    _profile().auxiliary_complete({"model": "m", "messages": [], "timeout": None})
    _profile().auxiliary_complete({"model": "m", "messages": [], "timeout": 0})
    assert seen == [None, None]


def test_only_a_profile_with_the_hook_has_a_completer():
    assert ac._profile_auxiliary_completer("cursor") is not None
    assert ac._profile_auxiliary_completer("anthropic") is None
    assert ac._profile_auxiliary_completer("") is None
    assert ac._profile_auxiliary_completer(None) is None


def _plan(monkeypatch, provider, kwargs=None):
    req = SimpleNamespace(
        client=object(), kwargs=kwargs or {"model": "m", "messages": []}, request_provider=provider,
        resolved_provider=provider, final_model="m", effective_timeout=30,
    )
    monkeypatch.setattr(ac, "_plan_aux_call", lambda *a, **k: (req, {}, {}))
    monkeypatch.setattr(ac, "_validate_llm_response", lambda resp, task=None, **k: resp)


def test_call_llm_routes_a_cursor_text_request_through_the_profile(monkeypatch):
    _plan(monkeypatch, "cursor", {"model": "composer-2.5-fast", "messages": [{"role": "user", "content": "hi"}]})
    got = []
    monkeypatch.setattr(
        ac, "_profile_auxiliary_completer", lambda provider: lambda request: got.append(request) or "answer"
    )
    assert ac._call_llm_impl("title_generation", messages=[{"role": "user", "content": "hi"}]) == "answer"
    assert got and got[0]["model"] == "composer-2.5-fast"


@pytest.mark.parametrize("extra", [{"stream": True}, {"tools": [{"type": "function"}]}])
def test_streaming_and_tool_requests_do_not_use_the_completer(monkeypatch, extra):
    _plan(monkeypatch, "cursor")
    monkeypatch.setattr(
        ac, "_profile_auxiliary_completer", lambda provider: pytest.fail("a streaming or tool call must not reach it")
    )
    with pytest.raises(Exception) as exc:
        ac._call_llm_impl("x", messages=[], **extra)
    assert "must not reach it" not in str(exc.value)
