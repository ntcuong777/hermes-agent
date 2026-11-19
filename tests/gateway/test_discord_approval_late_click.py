"""A Discord approval card must outlive the wait it answers, and a click nobody answers must not
end in Discord's "This interaction failed".

Before: the card's view timed out at ``approvals.discord_prompt_timeout`` (300 s) while the agent kept
waiting for ``approvals.timeout`` (here: a year). The card was retired as "expired" with the agent still
blocked, and a click on a card whose view was gone reached no callback at all.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.deadline import MAX_SAFE_TIMEOUT_S
from agent.i18n import t
from gateway.config import PlatformConfig
from plugins.platforms.discord import adapter as discord_adapter
from plugins.platforms.discord.adapter import DiscordAdapter, ExecApprovalView
from plugins.platforms.discord.adapter_config_readers import _read_exec_approval_view_timeout


@pytest.fixture(autouse=True)
def _install_discord_wait(monkeypatch):
    import sys
    sys.path.insert(0, "/private/etc/nix-darwin/.worktrees/core-surface-all/hermes/plugins/discord-approval-wait")
    import daw_timeout
    import plugins.platforms.discord.adapter_exec_approval_view as view
    daw_timeout.install(monkeypatch)
    monkeypatch.setattr(view, "_read_exec_approval_view_timeout", daw_timeout.read_exec_approval_view_timeout)
    monkeypatch.setattr(
        sys.modules[__name__], "_read_exec_approval_view_timeout", daw_timeout.read_exec_approval_view_timeout)


def _config(monkeypatch, *, approvals_timeout=None, discord_prompt_timeout=None):
    raw = {k: v for k, v in (("timeout", approvals_timeout),
                             ("discord_prompt_timeout", discord_prompt_timeout)) if v is not None}
    import hermes_cli.config
    monkeypatch.setattr(hermes_cli.config, "read_raw_config", lambda: {"approvals": raw})
    return patch("tools.approval_context._get_approval_config", return_value=raw)


def test_card_follows_a_long_approval_timeout_when_the_prompt_timeout_is_unset(monkeypatch):
    with _config(monkeypatch, approvals_timeout=31536000):
        assert _read_exec_approval_view_timeout() == int(MAX_SAFE_TIMEOUT_S)
        assert ExecApprovalView(session_key="s", allowed_user_ids=set()).timeout == int(MAX_SAFE_TIMEOUT_S)


def test_card_uses_the_prompt_timeout_when_nothing_is_longer(monkeypatch):
    with _config(monkeypatch):
        assert _read_exec_approval_view_timeout() == 300


def test_card_never_dies_before_its_approval_wait(monkeypatch):
    with _config(monkeypatch, approvals_timeout=7200, discord_prompt_timeout=300):
        assert _read_exec_approval_view_timeout() == 7200


def test_prompt_timeout_still_extends_a_card_beyond_a_short_approval_wait(monkeypatch):
    with _config(monkeypatch, approvals_timeout=600, discord_prompt_timeout=3600):
        assert _read_exec_approval_view_timeout() == 3600


def test_default_approval_timeout_is_five_minutes():
    from tools.approval_context import _get_approval_timeout
    with patch("tools.approval_context._get_approval_config", return_value={}):
        assert _get_approval_timeout() == 300


class _FakeView:
    def __init__(self, finished=False):
        self._finished = finished

    def is_finished(self):
        return self._finished


def _interaction(*, done, kind="component", message_id=42, custom_id="cid"):
    component = discord_adapter.discord.InteractionType.component
    return SimpleNamespace(
        type=component if kind == "component" else object(),
        data={"custom_id": custom_id, "component_type": 2},
        message=SimpleNamespace(id=message_id),
        response=SimpleNamespace(is_done=lambda: done, send_message=AsyncMock()))


def _client(views=None, dynamic=()):
    """A discord client whose view store holds ``views``: {message_id: {(type, custom_id): view}}."""
    store = SimpleNamespace(
        _views={mid: {key: SimpleNamespace(view=view) for key, view in items.items()}
                for mid, items in (views or {}).items()},
        _dynamic_items={pattern: object() for pattern in dynamic})
    return SimpleNamespace(_connection=SimpleNamespace(_view_store=store))


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(DiscordAdapter, "_ORPHAN_COMPONENT_GRACE_S", 0)
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._client = _client()
    return adapter


@pytest.mark.asyncio
async def test_unanswered_click_on_an_approval_card_with_no_view_gets_the_approval_note(adapter):
    adapter._approval_card_message_ids.append("42")
    interaction = _interaction(done=False)
    await adapter._answer_orphaned_component(interaction)
    interaction.response.send_message.assert_awaited_once_with(
        t("platform.discord.approval.no_longer_pending"), ephemeral=True)


@pytest.mark.asyncio
async def test_unanswered_click_on_another_prompt_gets_the_generic_expired_note(adapter):
    interaction = _interaction(done=False)
    await adapter._answer_orphaned_component(interaction)
    interaction.response.send_message.assert_awaited_once_with(
        t("platform.discord.prompt.expired_footer"), ephemeral=True)


@pytest.mark.asyncio
async def test_a_slow_live_view_is_never_raced(adapter):
    """is_done() is still False because the view's ack HTTP call has not returned: the view owns it."""
    adapter._client = _client({42: {(2, "cid"): _FakeView()}})
    interaction = _interaction(done=False)
    await adapter._answer_orphaned_component(interaction)
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_persistent_view_without_a_message_id_is_live(adapter):
    adapter._client = _client({None: {(2, "cid"): _FakeView()}})
    interaction = _interaction(done=False)
    await adapter._answer_orphaned_component(interaction)
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_finished_view_is_orphaned(adapter):
    adapter._client = _client({42: {(2, "cid"): _FakeView(finished=True)}})
    interaction = _interaction(done=False)
    await adapter._answer_orphaned_component(interaction)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_dynamic_item_click_is_live(adapter):
    import re
    adapter._client = _client(dynamic=[re.compile("c.d")])
    interaction = _interaction(done=False)
    await adapter._answer_orphaned_component(interaction)
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unreadable_view_store_stays_silent(adapter):
    adapter._client = None
    interaction = _interaction(done=False)
    await adapter._answer_orphaned_component(interaction)
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_click_already_answered_stays_silent(adapter):
    interaction = _interaction(done=True)
    await adapter._answer_orphaned_component(interaction)
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_component_interactions_are_ignored(adapter):
    interaction = _interaction(done=False, kind="slash")
    await adapter._answer_orphaned_component(interaction)
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_before_the_ack_still_answers_the_clicker():
    view = ExecApprovalView(session_key="s", allowed_user_ids=set())
    view._gate = AsyncMock(side_effect=RuntimeError("auth lookup failed"))
    interaction = SimpleNamespace(
        user=SimpleNamespace(display_name="tester", id=1),
        response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))

    with pytest.raises(RuntimeError, match="auth lookup failed"):
        await view._resolve(interaction, "once", 2, "platform.discord.approval.resolved_once")

    interaction.response.send_message.assert_awaited_once_with(
        t("platform.discord.approval.click_failed"), ephemeral=True)


@pytest.mark.asyncio
async def test_failure_after_the_ack_does_not_answer_twice():
    view = ExecApprovalView(session_key="s", allowed_user_ids=set())
    view._gate = AsyncMock(side_effect=RuntimeError("late failure"))
    interaction = SimpleNamespace(
        user=SimpleNamespace(display_name="tester", id=1),
        response=SimpleNamespace(is_done=lambda: True, send_message=AsyncMock()))

    with pytest.raises(RuntimeError):
        await view._resolve(interaction, "once", 2, "platform.discord.approval.resolved_once")

    interaction.response.send_message.assert_not_awaited()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_recorded_choice_whose_card_edit_fails_does_not_say_click_again():
    view = ExecApprovalView(session_key="s", allowed_user_ids=set(), request_id="r1")
    view._gate = AsyncMock(return_value=True)
    view._finalize_embed = AsyncMock(side_effect=RuntimeError("edit_message failed"))
    view._expire_embed = AsyncMock()  # the real retire() runs and stamps the card via the message
    interaction = SimpleNamespace(
        user=SimpleNamespace(display_name="tester", id=1),
        response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))

    with patch("gateway.approval_actor.approval_refusal", return_value=None), \
            patch("tools.approval.resolve_gateway_approval_card", return_value=1):
        with pytest.raises(RuntimeError, match="edit_message failed"):
            await view._resolve(interaction, "once", 2, "platform.discord.approval.resolved_once")

    view._expire_embed.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
