"""A goal/loop change made through a messaging lane repaints the local viewer bound to that session.

The TUI's goal and loop bars follow ``session.control.update``. A ``/goal clear`` sent to a Discord thread
a TUI session is mirrored onto ran in the lane, which only published for LOCAL sources, so the TUI kept
showing the cleared goal's "paused" bar until it was reopened.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform
from gateway.session import SessionSource


def _runner():
    from gateway.run import GatewayRunner
    class _Runner(GatewayRunner):  # the session store is a property on the real runner
        async_session_store = SimpleNamespace(
            get_or_create_session=AsyncMock(return_value=SimpleNamespace(session_id='local-s1')))
    return object.__new__(_Runner)


def _source(platform, chat_id='thread-1'):
    return SessionSource(platform=platform, chat_id=chat_id, chat_type='thread', user_id='u')


def _published():
    """Patch the publisher: returns the AsyncMock recording ``publish_async(authority, session_id, ...)``."""
    publish = AsyncMock()
    return publish, patch('gateway.session_control_actions.publish_async', publish), \
        patch('gateway.session_authorities.active_authority', lambda _runner: 'authority')


@pytest.mark.asyncio
async def test_a_lane_source_publishes_to_the_session_it_is_bound_to():
    publish, p1, p2 = _published()
    with p1, p2:
        await _runner()._publish_local_control(_source(Platform.DISCORD), session_id='local-s1')
    publish.assert_awaited_once_with('authority', 'local-s1', only_if_present=False)


@pytest.mark.asyncio
async def test_a_local_source_still_publishes_its_own_chat_id():
    publish, p1, p2 = _published()
    with p1, p2:
        await _runner()._publish_local_control(_source(Platform.LOCAL, 'local-s2'), only_if_present=True)
    publish.assert_awaited_once_with('authority', 'local-s2', only_if_present=True)


@pytest.mark.asyncio
async def test_a_lane_without_a_known_session_publishes_nothing():
    publish, p1, p2 = _published()
    with p1, p2:
        await _runner()._publish_local_control(_source(Platform.DISCORD))
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lane_goal_command_repaints_the_bound_viewer():
    """The real /subgoal handler (the no-goal path) goes through the same wrapper as /goal, /loop, /heartbeat."""
    runner = _runner()
    mgr = SimpleNamespace(has_goal=lambda: False, session_id='local-s1')
    runner._get_goal_manager_for_event = AsyncMock(return_value=(mgr, SimpleNamespace(session_id='local-s1')))
    event = SimpleNamespace(source=_source(Platform.DISCORD), get_command_args=lambda: '')
    publish, p1, p2 = _published()
    with p1, p2:
        out = await runner._handle_subgoal_command(event)
    assert out  # the command's own reply is untouched
    publish.assert_awaited_once_with('authority', 'local-s1', only_if_present=False)


@pytest.mark.asyncio
async def test_a_local_goal_command_is_not_published_twice():
    """A local session's execute_command already publishes after the command: the lane wrapper stays out."""
    runner = _runner()
    mgr = SimpleNamespace(has_goal=lambda: False, session_id='local-s1')
    runner._get_goal_manager_for_event = AsyncMock(return_value=(mgr, SimpleNamespace(session_id='local-s1')))
    event = SimpleNamespace(source=_source(Platform.LOCAL, 'local-s1'), get_command_args=lambda: '')
    publish, p1, p2 = _published()
    with p1, p2:
        await runner._handle_subgoal_command(event)
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_publish_failure_never_breaks_the_command():
    runner = _runner()
    mgr = SimpleNamespace(has_goal=lambda: False, session_id='local-s1')
    runner._get_goal_manager_for_event = AsyncMock(return_value=(mgr, SimpleNamespace(session_id='local-s1')))
    event = SimpleNamespace(source=_source(Platform.DISCORD), get_command_args=lambda: '')
    with patch('gateway.session_control_actions.publish_async', AsyncMock(side_effect=RuntimeError('boom'))), \
            patch('gateway.session_authorities.active_authority', lambda _runner: 'authority'):
        assert await runner._handle_subgoal_command(event)
