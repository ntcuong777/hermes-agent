"""A staged skill write must be one the person can approve.

The write gate stages a skill write before any content check ran, so the review fork's
creates with a 178-char description (index budget: 60), a YAML-broken frontmatter or a patch
whose old_string no longer matches sat in ``pending/skills`` and failed only at
``/skills approve``, where the person cannot fix them (every pending skill write errored).
Staging now refuses what could never apply, and an approved replay waives the two checks that
guard the model's output rather than the file's safety (description budget; a patch to a file
that was already over the size cap).
"""
import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest

LONG_DESC = "Use when " + "probing the approve flow of a staged skill write " * 4
SK = "---\nname: {n}\ndescription: {d}\n---\n# Probe\nStep 1.\n"


@pytest.fixture
def smt(tmp_path, monkeypatch):
    """skill_manager_tool bound to a temp HERMES_HOME with the skills write gate on."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "skills").mkdir()
    import tools.skill_manager_tool as module
    module = importlib.reload(module)
    import tools.write_approval as wa
    monkeypatch.setattr(wa, "write_approval_enabled", lambda subsystem: True)
    return module


def _manage(smt, ops):
    return json.loads(smt.skill_manage(action="", name="", operations=ops))


def _pending():
    import tools.write_approval as wa
    return wa.list_pending(wa.SKILLS)


def _approve(*args):
    from hermes_cli.write_approval_commands import handle_pending_subcommand
    import tools.write_approval as wa
    return handle_pending_subcommand(wa.SKILLS, ["approve", *args])


def _stage_raw(ops, origin="background_review"):
    """A record as the queue holds one that predates the staging precheck."""
    import tools.write_approval as wa
    return wa.stage_write(wa.SKILLS, {"action": "batch", "operations": ops}, summary="legacy", origin=origin)


def test_staging_refuses_a_description_over_the_index_budget(smt):
    r = _manage(smt, [{"action": "create", "name": "probe", "content": SK.format(n="probe", d=LONG_DESC)}])
    assert r["success"] is False and not r.get("staged")
    assert "Not staged" in r["error"] and "Description is" in r["error"]
    assert _pending() == []


def test_staging_refuses_a_broken_frontmatter(smt):
    bad = SK.format(n="probe", d="Use when a colon: breaks the YAML mapping.")
    r = _manage(smt, [{"action": "create", "name": "probe", "content": bad}])
    assert r["success"] is False and "YAML frontmatter parse error" in r["error"]
    assert _pending() == []


def test_staging_refuses_a_patch_whose_old_string_is_gone(smt):
    ok = json.loads(smt.apply_skill_pending(
        {"action": "create", "name": "probe", "content": SK.format(n="probe", d="Use when probing.")}))
    assert ok["success"], ok
    pending_before = len(_pending())
    r = _manage(smt, [{"action": "patch", "name": "probe", "old_string": "not in the file", "new_string": "x"}])
    assert r["success"] is False and "Could not find a match" in r["error"]
    assert len(_pending()) == pending_before


def test_staging_refuses_a_create_over_an_existing_skill(smt):
    import tools.write_approval as wa
    # Land one for real by replaying (the gate is bypassed), then try to stage the same name again.
    ok = json.loads(smt.apply_skill_pending(
        {"action": "create", "name": "probe", "content": SK.format(n="probe", d="Use when probing.")}))
    assert ok["success"], ok
    r = _manage(smt, [{"action": "create", "name": "probe", "content": SK.format(n="probe", d="Use when probing.")}])
    assert r["success"] is False and "already exists" in r["error"]
    assert wa.list_pending(wa.SKILLS) == []


def test_a_valid_write_still_stages_and_approves(smt, tmp_path):
    r = _manage(smt, [
        {"action": "create", "name": "probe", "content": SK.format(n="probe", d="Use when probing.")},
        {"action": "write_file", "name": "probe", "file_path": "references/a.md", "file_content": "a"}])
    assert r.get("staged") is True, r
    out = _approve("all")
    assert "Approved 1 skills write(s)." in out and "Failed" not in out
    assert (tmp_path / "skills" / "probe" / "references" / "a.md").read_text() == "a"
    assert _pending() == []


def test_approve_applies_a_staged_create_whose_description_is_over_budget(smt, tmp_path):
    """The queue the person had: creates staged with a 144-183 char description."""
    _stage_raw([{"action": "create", "name": "probe", "content": SK.format(n="probe", d=LONG_DESC)}])
    out = _approve("all")
    assert "Approved 1 skills write(s)." in out and "Failed" not in out, out
    assert LONG_DESC in (tmp_path / "skills" / "probe" / "SKILL.md").read_text()


def test_a_direct_write_keeps_the_description_budget(smt, monkeypatch):
    """The waiver is the approver's alone: an unstaged write by the model is still held to it."""
    import tools.write_approval as wa
    monkeypatch.setattr(wa, "write_approval_enabled", lambda subsystem: False)
    r = _manage(smt, [{"action": "create", "name": "probe", "content": SK.format(n="probe", d=LONG_DESC)}])
    assert r["success"] is False and "Description is" in r["error"]


def test_approve_patches_a_supporting_file_that_was_already_over_the_cap(smt, tmp_path):
    cap = smt.MAX_SKILL_CONTENT_CHARS
    ok = json.loads(smt.apply_skill_pending(
        {"action": "create", "name": "probe", "content": SK.format(n="probe", d="Use when probing.")}))
    assert ok["success"], ok
    big = tmp_path / "skills" / "probe" / "references"
    big.mkdir()
    (big / "k.md").write_text("MARK\n" + "x" * (cap + 20))
    _stage_raw([{"action": "patch", "name": "probe", "file_path": "references/k.md",
                 "old_string": "MARK", "new_string": "MARKED " + "y" * 50}])
    out = _approve("all")
    assert "Approved 1 skills write(s)." in out and "Failed" not in out, out
    assert (big / "k.md").read_text().startswith("MARKED ")


def test_a_stale_staged_patch_fails_with_what_to_do_and_stays_pending(smt):
    ok = json.loads(smt.apply_skill_pending(
        {"action": "create", "name": "probe", "content": SK.format(n="probe", d="Use when probing.")}))
    assert ok["success"], ok
    record = _stage_raw([{"action": "patch", "name": "probe", "old_string": "gone " * 200, "new_string": "x"}])
    out = _approve(record["id"])
    assert "Approved 0 skills write(s)." in out and "Failed:" in out
    assert "/skills reject <id>" in out and "stay pending" in out
    assert max(len(line) for line in out.splitlines()) < 900  # the file excerpt is clipped
    assert [r["id"] for r in _pending()] == [record["id"]]


def test_the_gateway_skills_handler_approves_and_rejects_through_the_same_path(smt, tmp_path):
    from gateway.slash_commands import GatewaySlashCommandsMixin as Mixin

    class Event:
        source = None

        def __init__(self, args):
            self.args = args

        def get_command_args(self):
            return self.args

    fake = SimpleNamespace(_write_approval_setter=lambda *a, **k: None)
    run = lambda args: asyncio.run(Mixin._handle_skills_command(fake, Event(args)))
    keep = _stage_raw([{"action": "create", "name": "keep", "content": SK.format(n="keep", d=LONG_DESC)}])
    drop = _stage_raw([{"action": "create", "name": "drop", "content": SK.format(n="drop", d=LONG_DESC)}])
    assert "Approved 1 skills write(s)." in run(f"approve {keep['id']}")
    assert "Rejected pending skills write" in run(f"reject {drop['id']}")
    assert (tmp_path / "skills" / "keep" / "SKILL.md").exists()
    assert not (tmp_path / "skills" / "drop").exists() and _pending() == []
