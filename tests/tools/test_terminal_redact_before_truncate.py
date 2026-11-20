"""Terminal output is redacted whole and truncated after: a secret that straddles the head/tail cut must not
survive as two fragments that no redaction pattern matches."""

from tools import terminal_tool_result as ttr

SECRET = "sk-" + "proj" + "A1b2C3d4E5f6" * 4


def test_the_truncator_only_ever_sees_redacted_text(monkeypatch):
    seen = []

    def spy(text):
        seen.append(text)
        return text

    monkeypatch.setattr(ttr, "_truncate_head_tail", spy)
    monkeypatch.setattr(ttr, "_interpret_exit_code", lambda *a, **k: None)
    monkeypatch.setattr(ttr, "_failure_hint", lambda *a, **k: None)
    env = type("Env", (), {"cwd": "/tmp"})()
    try:
        ttr.finalize_foreground_result(
            command="cat notes.txt", result={"output": f"token is {SECRET} end", "returncode": 0}, env=env,
            env_type="local", effective_task_id="t", task_id="t", session_id="s", session_key="k",
            workdir=None, command_cwd=None, approval_note=None)
    except Exception:
        pass  # only the order of the two steps is under test; later bookkeeping may need a fuller env
    assert seen, "the truncation step never ran"
    assert SECRET not in seen[0] and SECRET[3:15] not in seen[0]
