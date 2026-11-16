import pytest
from types import SimpleNamespace
from tests.gateway.test_lane_keeps_local_model import PROFILE, _home, _bind, _gateway_runner  # noqa
from tests.gateway.test_authority_cold_route_recovery import _source

pytestmark = pytest.mark.usefixtures("authority_cwd_in_tmp")


@pytest.mark.asyncio
async def test_scratch(tmp_path):
    store, route, sid = await _bind(tmp_path, {'model': 'model-a', 'provider': 'anthropic', 'reasoning': 'high'})
    runner = _gateway_runner(store)
    live = SimpleNamespace(source=_source('discord', chat_id='thread-1'), route=route)
    from gateway.session_messaging_picks import messaging_route_info
    try:
        print("INFO", messaging_route_info(runner, live))
    except Exception:
        import traceback; print("RAISED"); traceback.print_exc()
    assert False
