"""Plugin composer directives survive the shared gateway's slash route."""

import asyncio

from tests.gateway.fixtures.local_recovery_probe import daemon, rpc, websocket
from tests.gateway.test_cli_plugin_command_in_gateway import _plugin_home


_PLUGIN = """
def register(ctx):
    def stash(arg):
        if arg.startswith("push-draft"):
            return {"type": "stash-saved", "count": 1, "max": 9, "notice": "Saved"}
        return {"type": "prefill", "message": "saved draft", "notice": "Restored"}
    ctx.register_command("stash", stash, description="Composer fixture")
"""


def test_stash_save_and_restore_keep_directives_over_slash_rpc(tmp_path):
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
                        command='stash push-draft "saved draft"',
                    )
                    assert saved["result"] == {
                        "type": "stash-saved",
                        "count": 1,
                        "max": 9,
                        "notice": "Saved",
                    }
                    restored = await rpc(
                        ws, "slash.exec", session_id=sid, command="stash peek"
                    )
                    assert restored["result"] == {
                        "type": "prefill",
                        "message": "saved draft",
                        "notice": "Restored",
                    }

            asyncio.run(probe())
