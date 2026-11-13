"""Telegram topic ``workspace`` bindings: like ``skill``, a dm_topics / group_topics entry can pin
the host directory a NEW session in that topic starts in. Resolved alongside the skill on every
inbound message and carried to the gateway as ``MessageEvent.auto_workspace``.
"""

import sys
from types import SimpleNamespace

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.event import MessageType


from tests.gateway.conftest import _ensure_telegram_mock

_ensure_telegram_mock()
# Force reimport so the adapter binds to whatever sys.modules now holds (the shared mock, or the
# real library when it is installed) — same reasoning as tests/gateway/test_dm_topics.py.
sys.modules.pop("plugins.platforms.telegram.adapter", None)

from plugins.platforms.telegram.adapter import TelegramAdapter


def _make_adapter(dm_topics_config=None, group_topics_config=None):
    extra = {}
    if dm_topics_config is not None:
        extra["dm_topics"] = dm_topics_config
    if group_topics_config is not None:
        extra["group_topics"] = group_topics_config
    return TelegramAdapter(PlatformConfig(enabled=True, token="***", extra=extra))


def _message(chat_id):
    """Minimal message for _resolve_topic_binding (no forum_topic_created service message)."""
    return SimpleNamespace(chat=SimpleNamespace(id=chat_id))


def _dm_message(chat_id=111, thread_id=100, user_name="Test User"):
    """Same shape tests/gateway/test_dm_topics.py uses for _build_message_event."""
    chat = SimpleNamespace(id=chat_id, type="private", title=None, full_name=user_name)
    user = SimpleNamespace(id=42, full_name=user_name)
    return SimpleNamespace(
        chat=chat, from_user=user, text="hello", message_thread_id=thread_id,
        is_topic_message=bool(thread_id), message_id=1001, reply_to_message=None,
        date=None, forum_topic_created=None,
    )


def test_dm_topic_config_yields_the_workspace():
    adapter = _make_adapter(dm_topics_config=[
        {"chat_id": 222, "topics": [
            {"name": "Research", "skill": "arxiv", "workspace": "~/code/papers"},
            {"name": "General"},
        ]},
    ])
    adapter._dm_topics = {"222:Research": 999, "222:General": 1}
    message = _message(222)

    assert adapter._resolve_topic_binding(message, "dm", "999") == ("Research", "arxiv", "~/code/papers")
    assert adapter._resolve_topic_binding(message, "dm", "1") == ("General", None, None)


def test_group_forum_topic_config_yields_the_workspace():
    adapter = _make_adapter(group_topics_config=[
        {"chat_id": -100333, "topics": [
            {"name": "Bugs", "thread_id": 12, "skill": "fix", "workspace": "/home/me/repo"},
        ]},
    ])
    message = _message(-100333)

    assert adapter._resolve_topic_binding(message, "group", "12") == ("Bugs", "fix", "/home/me/repo")
    assert adapter._resolve_topic_binding(message, "group", "99") == (None, None, None)


def test_build_message_event_sets_auto_workspace():
    """The topic's workspace rides the event like auto_skill, so the gateway's new-session seed
    can consume it without knowing Telegram shapes."""
    adapter = _make_adapter(dm_topics_config=[
        {"chat_id": 111, "topics": [
            {"name": "My Project", "workspace": "~/code/papers", "thread_id": 100},
        ]},
    ])
    adapter._dm_topics["111:My Project"] = 100

    event = adapter._build_message_event(_dm_message(chat_id=111, thread_id=100), MessageType.TEXT)

    assert event.auto_workspace == "~/code/papers"
    assert event.auto_skill is None


def test_build_message_event_without_binding_leaves_auto_workspace_none():
    adapter = _make_adapter(dm_topics_config=[
        {"chat_id": 111, "topics": [{"name": "General", "thread_id": 200}]},
    ])
    adapter._dm_topics["111:General"] = 200

    event = adapter._build_message_event(_dm_message(chat_id=111, thread_id=200), MessageType.TEXT)

    assert event.auto_workspace is None
