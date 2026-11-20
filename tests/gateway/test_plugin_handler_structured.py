"""A local client keeps a plugin command's composer directive even when the session came from a messaging platform."""

import asyncio
import contextlib
import types

from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.run_inbound_hooks import GatewayInboundHooksMixin as Hooks  # noqa: N814
from gateway.session import SessionSource

DIRECTIVE = {"type": "composer-clear", "notice": "stashed"}


def _run(platform, **kw):
    source = SessionSource(platform=platform, chat_id="1", chat_type="thread")
    runner = types.SimpleNamespace(
        session_store=None, config=GatewayConfig(), _session_key_for_source=lambda _s: "key",
        _session_env_scope=lambda _c: contextlib.nullcontext())

    async def in_executor(call):
        return call()

    runner._run_in_executor_with_context = in_executor
    event = MessageEvent(text="/stash toggle", source=source)
    return asyncio.run(Hooks._hm_run_plugin_handler(runner, lambda arg, **_k: dict(DIRECTIVE), event, source, **kw))


def test_a_local_client_gets_the_directive_whatever_the_session_platform():
    assert _run(Platform.DISCORD, structured=True) == DIRECTIVE


def test_a_messaging_reply_still_becomes_text():
    assert _run(Platform.DISCORD) == str(DIRECTIVE)


def test_a_local_session_keeps_its_directive_as_before():
    assert _run(Platform.LOCAL) == DIRECTIVE
