"""Plugin absent: a skill write is staged the way upstream stages it.

The description budget still applies to a write that is not staged. An approved
replay of an over-budget description does not land. skills-staging is what
refuses the stage and waives that check on replay.
"""
import importlib
import json

import pytest

LONG_DESC = "Use when " + "probing the approve flow of a staged skill write " * 4
SK = "---\nname: {n}\ndescription: {d}\n---\n# Probe\nStep 1.\n"


@pytest.fixture
def smt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "skills").mkdir()
    import tools.skill_manager_tool as module
    module = importlib.reload(module)
    import tools.write_approval as wa
    monkeypatch.setattr(wa, "write_approval_enabled", lambda subsystem: True)
    return module


def test_an_over_budget_description_is_staged(smt):
    r = json.loads(smt.skill_manage(
        action="", name="",
        operations=[{"action": "create", "name": "probe", "content": SK.format(n="probe", d=LONG_DESC)}]))
    assert r.get("staged") is True, r
    import tools.write_approval as wa
    assert wa.list_pending(wa.SKILLS)


def test_a_direct_write_keeps_the_description_budget(smt, monkeypatch):
    import tools.write_approval as wa
    monkeypatch.setattr(wa, "write_approval_enabled", lambda subsystem: False)
    r = json.loads(smt.skill_manage(
        action="", name="",
        operations=[{"action": "create", "name": "probe", "content": SK.format(n="probe", d=LONG_DESC)}]))
    assert r["success"] is False and "Description is" in r["error"]


def test_an_approved_over_budget_description_does_not_land(smt, tmp_path):
    import tools.write_approval as wa
    from hermes_cli.write_approval_commands import handle_pending_subcommand

    wa.stage_write(
        wa.SKILLS,
        {"action": "batch", "operations": [
            {"action": "create", "name": "probe", "content": SK.format(n="probe", d=LONG_DESC)}]},
        summary="legacy", origin="background_review")
    out = handle_pending_subcommand(wa.SKILLS, ["approve", "all"])
    assert "Failed" in out
    assert not (tmp_path / "skills" / "probe" / "SKILL.md").exists()


def test_a_staging_refusal_is_a_tool_error(smt):
    def boom(_wa):
        raise smt._StagingRefused("Not staged: probe")

    out = json.loads(smt._run_write_gate(boom))
    assert out["success"] is False and "Not staged: probe" in out["error"]
    import tools.write_approval as wa
    assert wa.list_pending(wa.SKILLS) == []
