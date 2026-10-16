"""Phone /status must use the same gateway override as turn routing, not the CLI provider."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.gateway.test_status_command import _make_event, _runner_with_session_override

CONFIG = {
    "model": {
        "default": "claude-sonnet-5-5",
        "provider": "anthropic",
        "context_length": 200_000,
    },
    "gateway": {
        "model": {
            "default": "gpt-6-luna",
            "provider": "openai-codex",
            "context_length": 1_050_000,
        }
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [{}, {"model": "gpt-6-luna"}])
async def test_cold_status_pairs_gateway_model_with_gateway_provider(row):
    runner = _runner_with_session_override({}, last_prompt_tokens=0)
    runner._session_db._db.get_session.return_value = row
    runner._session_db._db.get_recent_session_model_route.return_value = None
    with (
        patch("gateway.run._load_gateway_config", return_value=CONFIG),
        patch(
            "gateway.run._resolve_gateway_model_context",
            return_value=SimpleNamespace(
                context_length=1_050_000, context_source="detected"
            ),
        ),
    ):
        text = await runner._handle_status_command(_make_event("/status"))
    assert "`gpt-6-luna` (openai-codex)" in text, text
    assert "(anthropic)" not in text


def test_actual_runtime_resolver_already_uses_gateway_provider():
    from gateway.run import _resolve_gateway_model, _resolve_runtime_agent_kwargs

    with (
        patch("gateway.run._load_gateway_config", return_value=CONFIG),
        patch(
            "hermes_cli.runtime_provider.resolve_runtime_with_fallback",
            return_value=({"provider": "openai-codex"}, None),
        ) as resolver,
    ):
        runtime = _resolve_runtime_agent_kwargs()
    assert _resolve_gateway_model(CONFIG) == "gpt-6-luna"
    assert runtime["provider"] == "openai-codex"
    assert resolver.call_args.args[0]["model"] == CONFIG["gateway"]["model"]


def test_context_lookup_uses_gateway_context_pin_not_cli_pin():
    from gateway.run import _resolve_gateway_model_context

    with (
        patch("gateway.run._load_gateway_config", return_value=CONFIG),
        patch(
            "gateway.run._resolve_runtime_agent_kwargs",
            return_value={"provider": "openai-codex"},
        ),
        patch(
            "agent.model_metadata.get_model_context_length", return_value=1_050_000
        ) as lookup,
    ):
        resolved = _resolve_gateway_model_context("gpt-6-luna")
    assert resolved.provider == "openai-codex"
    assert lookup.call_args.kwargs["config_context_length"] == 1_050_000


@pytest.mark.parametrize(
    "gateway",
    [{}, {"model": {"provider": "openai-codex"}}, {"model": {"default": "gpt-6-luna"}}],
)
def test_incomplete_gateway_override_keeps_cli_model_provider_pair(gateway):
    from gateway.slash_commands_status import _status_model_route

    config = {"model": CONFIG["model"], "gateway": gateway}
    with patch("gateway.run._load_gateway_config", return_value=config):
        result = _status_model_route(
            None, {}, {}, {}, SimpleNamespace(last_prompt_tokens=0)
        )
    assert result[:2] == ("claude-sonnet-5-5", "anthropic")


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"model": "saved-other-model"}, ("saved-other-model", "")),
        ({"billing_provider": "saved-other-provider"}, ("", "saved-other-provider")),
        ({"billing_provider": "openai-codex"}, ("gpt-6-luna", "openai-codex")),
    ],
)
def test_partial_saved_route_only_inherits_matching_config_identity(row, expected):
    from gateway.slash_commands_status import _status_model_route

    with patch("gateway.run._load_gateway_config", return_value=CONFIG):
        result = _status_model_route(
            None, {}, {}, row, SimpleNamespace(last_prompt_tokens=0)
        )
    assert result[:2] == expected
