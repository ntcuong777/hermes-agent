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


def test_composer_text_may_be_null_on_the_wire():
    from gateway.session_commands import _parse
    assert _parse({"session_id": "s", "command": "stash toggle", "composer_text": None}, False) == ("stash", "toggle")
    assert _parse({"session_id": "s", "command": "stash toggle", "composer_text": "x"}, False) == ("stash", "toggle")


def test_only_composer_text_may_be_null_and_nothing_else_may_be_other_than_text():
    import pytest
    from gateway.session_commands import _parse
    from persistence.core.errors import AuthorityRefusal
    for bad in ({"session_id": "s", "command": None}, {"session_id": "s", "command": "x", "composer_text": 5},
                {"session_id": "s", "command": "x", "profile": None}):
        with pytest.raises(AuthorityRefusal):
            _parse(bad, False)


def test_only_a_well_formed_directive_passes_through_the_gateway():
    from gateway.session_commands import _is_directive
    assert _is_directive({"type": "prefill", "message": "m", "ack": "/x"})
    assert _is_directive({"type": "composer-clear", "notice": "n"})
    assert _is_directive({"type": "choices", "title": "t", "items": [{"label": "a", "select": "/s"}]})
    for bad in ({"type": "bogus"}, {"type": "prefill", "message": 5}, {"type": "prefill", "extra": 1},
                {"type": "choices", "items": [{"label": "a"}]}, {"type": 7}, {"message": "m"}, "text", None):
        assert not _is_directive(bad), bad
