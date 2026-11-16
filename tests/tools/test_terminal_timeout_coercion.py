"""A model sometimes sends ``timeout`` as a string. ``"30"`` must behave like 30, and a
non-number must come back as a tool error — before the fix both crashed the call with
``TypeError: '<=' not supported between instances of 'str' and 'int'``."""
import json

from tools import terminal_tool as tt


def _result(**kw):
    out = tt.terminal_tool(command="echo ok", **kw)
    return json.loads(out) if isinstance(out, str) else out


def test_string_timeout_is_coerced(monkeypatch):
    seen = {}
    real = tt._plan_execution

    def spy(command, **kw):
        seen['timeout'] = kw['timeout']
        return real(command, **kw)
    monkeypatch.setattr(tt, '_plan_execution', spy)
    result = _result(timeout="30")
    assert "'<=' not supported" not in json.dumps(result)
    assert seen['timeout'] == "30"  # the coercion lives inside the plan, at the one comparison


def test_non_numeric_timeout_is_a_tool_error():
    result = json.dumps(_result(timeout="soon"))
    assert "timeout must be a positive number of seconds" in result
    assert "'<=' not supported" not in result


def test_non_positive_string_timeout_is_rejected():
    assert "timeout must be a positive number of seconds" in json.dumps(_result(timeout="-5"))
