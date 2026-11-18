"""Stage B10: the thread and topic workspace bindings are gone from core (spec 08b stage B10); one seam line stays."""

import ast
from pathlib import Path

import gateway

ROOT = Path(gateway.__file__).resolve().parent.parent


def _text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_prepare_turn_hands_a_new_session_to_the_seam_in_one_line():
    source = _text("gateway/run_turn_prepare.py")
    node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_hmwa_prepare_turn")
    segment = ast.get_source_segment(source, node)
    assert segment.count(".session_opened(self, event, session_key, _is_new_session)") == 1
    assert "set_chat_workspace" not in segment and "auto_workspace" not in segment


def test_no_core_file_knows_the_workspace_binding_any_more():
    for rel in ("gateway/platforms/event.py", "gateway/session_terminal_cwd.py", "gateway/session_envelope.py",
                "plugins/platforms/discord/adapter.py", "plugins/platforms/telegram/adapter.py"):
        text = _text(rel)
        assert "auto_workspace" not in text and "set_chat_workspace" not in text and "get_chat_workspace" not in text, rel
        assert "_resolve_channel_workspace" not in text and "topic_workspace" not in text, rel
