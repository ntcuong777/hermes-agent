"""`cursor` is a selectable model provider whose turns run on the Cursor SDK worker (fork patch P41).

Before, nothing registered a `cursor` provider and the SDK runtime needed `model.cursor_runtime: sdk` in the config,
which would have moved the user's default model. Now `/model cursor:<id>` (or a profile naming the provider) is the
whole switch: the turn runs on the worker, Hermes executes every tool, and no other provider is affected.
"""

from __future__ import annotations

import io
import json

import pytest

from hermes_cli import runtime_provider as rp
from hermes_cli.models import parse_model_input
from providers import get_provider_profile


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "crsr_test_key")


def test_cursor_is_a_registered_provider_with_its_own_key_variable():
    profile = get_provider_profile("cursor")
    assert profile is not None and profile.env_vars == ("CURSOR_API_KEY",)


def test_picking_cursor_runs_the_turn_on_the_sdk_without_any_config_switch(monkeypatch):
    monkeypatch.setattr(rp, "_get_model_config", lambda: {"provider": "anthropic", "default": "claude-sonnet-5-5"})
    runtime = rp.resolve_runtime_provider(requested="cursor", target_model="composer-2")
    assert runtime["provider"] == "cursor" and runtime["api_mode"] == "cursor_sdk"


def test_a_user_can_still_pin_cursor_to_the_plain_chat_wire(monkeypatch):
    monkeypatch.setattr(rp, "_get_model_config", lambda: {"cursor_runtime": "chat"})
    runtime = rp.resolve_runtime_provider(requested="cursor", target_model="composer-2")
    assert runtime["api_mode"] != "cursor_sdk"


def test_other_providers_are_untouched(monkeypatch):
    monkeypatch.setattr(rp, "_get_model_config", lambda: {})
    assert rp._maybe_apply_cursor_sdk_runtime(
        provider="anthropic", api_mode="anthropic_messages", model_cfg={}, requested_provider="anthropic"
    ) == "anthropic_messages"


def test_model_input_names_the_provider():
    assert parse_model_input("cursor:composer-2", "anthropic") == ("cursor", "composer-2")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize(
    "body",
    [
        {"models": ["composer-2", "gpt-5.5"]},
        {"models": [{"id": "composer-2"}, {"id": "gpt-5.5"}]},
        {"items": [{"id": "composer-2"}, {"id": "gpt-5.5"}]},
    ],
)
def test_every_account_model_is_listed(monkeypatch, body):
    seen = {}

    def fake_open(req, timeout=0):
        seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
        return _Resp(json.dumps(body).encode())

    monkeypatch.setattr("hermes_cli.urllib_security.open_credentialed_url", fake_open)
    models = get_provider_profile("cursor").fetch_models(api_key="crsr_test_key")
    assert models == ["composer-2", "gpt-5.5"]
    assert seen["url"].startswith("https://api.cursor.com/") and seen["auth"].startswith("Basic ")


def test_a_failed_listing_falls_back_to_none_so_the_catalog_check_in_the_worker_decides(monkeypatch):
    def boom(req, timeout=0):
        raise OSError("blocked")

    monkeypatch.setattr("hermes_cli.urllib_security.open_credentialed_url", boom)
    assert get_provider_profile("cursor").fetch_models(api_key="crsr_test_key") is None
