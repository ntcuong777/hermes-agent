"""Plugin composer directives, and the composer text a key chord hands over, survive the shared gateway's slash route."""

import asyncio

from tests.gateway.fixtures.local_recovery_probe import daemon, rpc, websocket
from tests.gateway.test_cli_plugin_command_in_gateway import _plugin_home


_PLUGIN = """
def register(ctx):
    def stash(arg, composer_text=None):
        if arg.startswith("toggle"):
            if composer_text is None:
                return "no composer text"
            return {"type": "composer-clear", "notice": "Saved " + composer_text}
        if arg.startswith("open"):
            return {"type": "choices", "title": "Stash", "selected": 1,
                    "items": [{"label": "a", "select": "/stash peek", "drop": "/stash drop"}]}
        return {"type": "prefill", "message": "saved draft", "notice": "Restored", "ack": "/stash applied"}
    ctx.register_command("stash", stash, description="Composer fixture")
"""


def test_directives_and_composer_text_reach_the_client_over_slash_rpc(tmp_path):
    with _plugin_home(tmp_path, _PLUGIN) as (root, home, env):
        with daemon(root, home, env, barrier=False) as (_proc, desc):

            async def probe():
                async with websocket(home, desc) as ws:
                    created = await rpc(
                        ws,
                        "session.create",
                        request_id="stash",
                        source="tui",
                        toolsets=[],
                        cwd=str(tmp_path),
                    )
                    sid = created["result"]["session_id"]
                    saved = await rpc(
                        ws,
                        "slash.exec",
                        session_id=sid,
                        command="stash toggle",
                        composer_text="my draft",
                    )
                    assert saved["result"] == {"type": "composer-clear", "notice": "Saved my draft"}
                    typed = await rpc(ws, "slash.exec", session_id=sid, command="stash toggle")
                    assert typed["result"] == {"type": "exec", "output": "no composer text"}
                    opened = await rpc(ws, "slash.exec", session_id=sid, command="stash open")
                    assert opened["result"] == {
                        "type": "choices",
                        "title": "Stash",
                        "selected": 1,
                        "items": [{"label": "a", "select": "/stash peek", "drop": "/stash drop"}],
                    }
                    restored = await rpc(
                        ws, "slash.exec", session_id=sid, command="stash peek"
                    )
                    assert restored["result"] == {
                        "type": "prefill",
                        "message": "saved draft",
                        "notice": "Restored",
                        "ack": "/stash applied",
                    }

            asyncio.run(probe())
