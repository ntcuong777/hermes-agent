"""A goal/loop change made through a messaging lane repaints the local viewer bound to that session.

The TUI's goal and loop bars follow ``session.control.update``. A ``/goal clear`` sent to a Discord thread
a TUI session is mirrored onto ran in the lane, which only published for LOCAL sources, so the TUI kept
showing the cleared goal's "paused" bar until it was reopened.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


# ── session.info: every lane command repaints the bound viewer's pills ─────────────────────────────

import contextlib


def _info_runner(handler):
    from gateway.run import GatewayRunner

    class _R(GatewayRunner):
        async_session_store = SimpleNamespace(
            get_or_create_session=AsyncMock(return_value=SimpleNamespace(session_id='local-s1')))
        _handle_yolo_command = handler
    return object.__new__(_R)


def _info_published():
    publish = AsyncMock()
    return publish, patch('gateway.session_control_actions.publish_info_async', publish), \
        patch('gateway.session_authorities.active_authority', lambda _runner: 'authority')


@pytest.mark.asyncio
async def test_every_command_in_the_handler_table_repaints_the_bound_viewer():
    """The table wraps all handlers, so a command added later repaints too (a /model, /reasoning, /fast,
    /yolo or /title typed in the thread left the TUI's pills on their old values)."""
    async def yolo(self, event):
        return 'yolo on'
    runner = _info_runner(yolo)
    event = SimpleNamespace(source=_source(Platform.DISCORD))
    publish, p1, p2 = _info_published()
    with p1, p2:
        out = await runner._command_handler_table(('yolo',))['yolo'](event)
    assert out == 'yolo on'
    publish.assert_awaited_once_with('authority', 'local-s1')


@pytest.mark.asyncio
async def test_a_local_session_command_is_not_republished_by_the_table():
    async def yolo(self, event):
        return 'ok'
    runner = _info_runner(yolo)
    event = SimpleNamespace(source=_source(Platform.LOCAL, 'local-s1'))
    publish, p1, p2 = _info_published()
    with p1, p2:
        await runner._command_handler_table(('yolo',))['yolo'](event)
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_handler_does_not_republish_and_a_failing_publish_does_not_fail_the_command():
    async def boom(self, event):
        raise ValueError('nope')
    event = SimpleNamespace(source=_source(Platform.DISCORD))
    publish, p1, p2 = _info_published()
    with p1, p2, pytest.raises(ValueError):
        await _info_runner(boom)._command_handler_table(('yolo',))['yolo'](event)
    publish.assert_not_awaited()

    async def fine(self, event):
        return 'fine'
    with patch('gateway.session_control_actions.publish_info_async', AsyncMock(side_effect=RuntimeError('x'))), \
            patch('gateway.session_authorities.active_authority', lambda _runner: 'authority'):
        assert await _info_runner(fine)._command_handler_table(('yolo',))['yolo'](event) == 'fine'


@pytest.mark.asyncio
async def test_publish_info_sends_session_info_to_the_live_session_only():
    from gateway.session_control_actions import publish_info_async
    stream = SimpleNamespace(publish=lambda *a, **k: sent.append((a, k)))
    sent = []
    live = SimpleNamespace(event_stream=stream)
    authority = SimpleNamespace(sessions={'local-s1': live}, profile_id='p')
    with patch('gateway.session_local.local_session_info', lambda _a, _r: {'model': 'm'}), \
            patch('gateway.session_authorities.owner_scope', lambda _a: contextlib.nullcontext()):
        await publish_info_async(authority, 'local-s1')
        await publish_info_async(authority, 'absent')
        await publish_info_async(None, 'local-s1')
    assert sent == [(('local-s1', {'model': 'm'}), {'event_type': 'session.info'})]


@pytest.mark.asyncio
async def test_publish_info_drops_a_read_for_a_session_that_was_remounted():
    from gateway.session_control_actions import publish_info_async
    sent = []
    live = SimpleNamespace(event_stream=SimpleNamespace(publish=lambda *a, **k: sent.append(a)))
    authority = SimpleNamespace(sessions={'local-s1': live}, profile_id='p')

    def read(_a, _r):
        authority.sessions['local-s1'] = SimpleNamespace(event_stream=live.event_stream)  # re-mounted mid-read
        return {'model': 'old'}
    with patch('gateway.session_local.local_session_info', read), \
            patch('gateway.session_authorities.owner_scope', lambda _a: contextlib.nullcontext()):
        await publish_info_async(authority, 'local-s1')
    assert sent == []


# ── command.output: the lane's answer is shown to the bound viewer too ───────────────────────────────


@pytest.mark.asyncio
async def test_a_lane_commands_answer_reaches_the_bound_viewer_as_command_output():
    async def yolo(self, event):
        return 'YOLO mode is now on.'
    event = SimpleNamespace(source=_source(Platform.DISCORD), text='/yolo on\nsecond line')
    out = MagicMock()
    with patch('gateway.session_control_actions.publish_info_async', AsyncMock()), \
            patch('gateway.session_control_actions.publish_command_output', out), \
            patch('gateway.session_authorities.active_authority', lambda _runner: 'authority'):
        result = await _info_runner(yolo)._command_handler_table(('yolo',))['yolo'](event)
    assert result == 'YOLO mode is now on.'  # the thread still gets the reply from the runner
    out.assert_called_once_with('authority', 'local-s1', '/yolo on', 'YOLO mode is now on.')


@pytest.mark.asyncio
@pytest.mark.parametrize('answer', [None, '', '   \n'])
async def test_a_command_without_an_answer_publishes_no_output(answer):
    async def quiet(self, event):
        return answer
    event = SimpleNamespace(source=_source(Platform.DISCORD), text='/yolo')
    out = MagicMock()
    with patch('gateway.session_control_actions.publish_info_async', AsyncMock()), \
            patch('gateway.session_control_actions.publish_command_output', out), \
            patch('gateway.session_authorities.active_authority', lambda _runner: 'authority'):
        await _info_runner(quiet)._command_handler_table(('yolo',))['yolo'](event)
    out.assert_not_called()


def test_command_output_goes_to_the_live_session_and_is_bounded():
    from gateway.session_control_actions import COMMAND_OUTPUT_LIMIT, publish_command_output
    sent = []
    live = SimpleNamespace(event_stream=SimpleNamespace(publish=lambda *a, **k: sent.append((a, k))))
    authority = SimpleNamespace(sessions={'local-s1': live})
    publish_command_output(authority, 'local-s1', '/status', 'ok')
    publish_command_output(authority, 'absent', '/status', 'nobody listens')
    publish_command_output(None, 'local-s1', '/status', 'no authority')
    publish_command_output(authority, 'local-s1', '/big', 'x' * (COMMAND_OUTPUT_LIMIT + 50))
    assert sent[0] == (('local-s1', {'command': '/status', 'output': 'ok'}), {'event_type': 'command.output'})
    assert len(sent) == 2
    assert len(sent[1][0][1]['output']) == COMMAND_OUTPUT_LIMIT + 1 and sent[1][0][1]['output'].endswith('…')
