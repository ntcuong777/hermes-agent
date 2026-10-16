"""A gateway turn asks for the gateway block's provider, not the shared `model.provider` (B21).

The gateway block carries both halves of the phone route: `gateway.model.default` (the model) and
`gateway.model.provider`. The turn took the model from the block but let the resolver read the provider from the
global `model:` block, so a phone turn sent `gpt-6-luna` to Anthropic and died with HTTP 404.
"""

from __future__ import annotations

import pytest

import gateway.run as run
import hermes_cli.runtime_provider as rp

SHARED = {"provider": "anthropic", "default": "claude-sonnet-5-5"}
GATEWAY = {
    "provider": "openai-codex",
    "default": "gpt-6-luna",
    "reasoning_effort": "medium",
}


@pytest.fixture
def asked(monkeypatch):
    """What the ladder is asked for: the provider name and the model the route will send."""
    seen: dict = {}

    def ladder(requested, explicit_api_key, explicit_base_url, target_model):
        seen.update(requested=requested, target_model=target_model)
        yield {
            "provider": requested,
            "api_key": "k",
            "base_url": f"https://{requested}.example",
            "api_mode": "chat_completions",
            "source": "test",
        }

    monkeypatch.setattr(rp, "_ladder_rungs", ladder)
    return seen


def use(monkeypatch, cfg: dict) -> None:
    monkeypatch.setattr(run, "_load_gateway_config", lambda *a, **k: cfg)
    monkeypatch.setattr(rp, "load_config", lambda: cfg)


def test_a_gateway_turn_is_sent_to_the_gateway_provider(monkeypatch, asked):
    use(monkeypatch, {"model": SHARED, "gateway": {"model": GATEWAY}})

    kwargs = run._resolve_runtime_agent_kwargs()

    assert asked["requested"] == "openai-codex"
    assert kwargs["provider"] == "openai-codex"
    assert kwargs["base_url"] == "https://openai-codex.example"


def test_the_route_is_resolved_for_the_gateway_model(monkeypatch, asked):
    use(monkeypatch, {"model": SHARED, "gateway": {"model": GATEWAY}})

    run._resolve_runtime_agent_kwargs()

    assert asked["target_model"] == "gpt-6-luna"


def test_the_model_and_provider_of_a_turn_name_the_same_route(monkeypatch, asked):
    use(monkeypatch, {"model": SHARED, "gateway": {"model": GATEWAY}})

    model = run._resolve_gateway_model()
    provider = run._resolve_runtime_agent_kwargs()["provider"]

    assert (model, provider) == ("gpt-6-luna", "openai-codex")


def test_without_a_gateway_block_the_shared_route_is_used(monkeypatch, asked):
    use(monkeypatch, {"model": SHARED})

    kwargs = run._resolve_runtime_agent_kwargs()

    assert kwargs["provider"] == "anthropic"
    assert asked["requested"] == "anthropic"


def test_an_incomplete_gateway_block_falls_back_to_the_shared_route(monkeypatch, asked):
    use(monkeypatch, {"model": SHARED, "gateway": {"model": {"default": "gpt-6-luna"}}})

    kwargs = run._resolve_runtime_agent_kwargs()

    assert kwargs["provider"] == "anthropic"
