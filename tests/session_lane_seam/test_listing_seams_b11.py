"""The session-list seam kinds of spec 08b stage B11 (`list_params`, `list_rows`, `list_row`): fail-open with no plugin."""

from gateway import session_lane_seam as seam
from persistence.core.errors import AuthorityRefusal


def test_the_three_kinds_are_declared():
    for kind in ("list_params", "list_rows", "list_row"):
        assert kind in seam.KINDS


def test_with_no_plugin_the_params_pass_through_so_a_cwd_is_refused_by_the_core():
    params = {"cwd": "/x", "limit": 5}
    assert seam.list_params(params) is params


def test_with_no_plugin_every_row_is_listed():
    rows = [{"id": 1}, {"id": 2}]
    assert seam.list_rows(rows, {"cwd": "/x"}) is rows
    assert seam.list_row({"id": 1}, "/x") is True


def test_a_plugin_dict_answer_replaces_the_params(monkeypatch):
    monkeypatch.setattr(seam, "route_sync", lambda kind, **ctx: {"workspace": "/w"} if kind == "list_params" else None)
    assert seam.list_params({"cwd": "/x"}) == {"workspace": "/w"}


def test_a_plugin_false_drops_a_live_row(monkeypatch):
    monkeypatch.setattr(seam, "route_sync", lambda kind, **ctx: False if kind == "list_row" else None)
    assert seam.list_row({"id": 1}, "/w") is False


def test_a_refusal_returned_by_a_plugin_is_raised_not_swallowed():
    import pytest

    with pytest.raises(AuthorityRefusal) as info:
        seam._first([AuthorityRefusal("invalid_params")])
    assert info.value.reason == "invalid_params"
