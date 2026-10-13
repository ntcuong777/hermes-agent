"""A `/skill` named in the middle of a prompt reaches the model as a pointer, not as the whole skill.

A leading `/skill` loads the full skill into the turn. One typed after prose ("fix this with /tdd,
then /review") is plain text to the submit path, so the model never learned the skill existed.
`inline_skill_note` lists each such skill by name and one-line description and tells the model to
`skill_view` the ones that apply: the context stays small, and the model decides what to load.
"""

from __future__ import annotations

import pytest

import agent.skill_commands as sc
from tests.agent.test_skill_commands import _make_skill
from tools import skills_tool


@pytest.fixture
def skills(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    _make_skill(
        root, "tdd", body="Write the failing test FIRST. Use /secret-skill never."
    )
    _make_skill(root, "audit")
    _make_skill(root, "usr")
    _make_skill(root, "secret-skill")
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", root)
    commands = sc.scan_skill_commands()
    monkeypatch.setattr(sc, "get_interactive_skill_commands", lambda: commands)
    return commands


def test_a_mid_prompt_skill_is_listed_with_its_description(skills):
    note = sc.inline_skill_note("please fix this with /tdd before merging")
    assert "/tdd" in note and "Description for tdd." in note
    assert "skill_view" in note


def test_the_skill_body_is_not_loaded(skills):
    assert "failing test FIRST" not in sc.inline_skill_note("fix this with /tdd")


def test_several_skills_are_listed_once_each_in_order(skills):
    note = sc.inline_skill_note("do /audit then /tdd and /audit again")
    assert note.index("/audit") < note.index("/tdd")
    assert note.count("- /audit") == 1 and note.count("- /tdd") == 1


@pytest.mark.parametrize(
    "text",
    [
        "look at /usr/local/bin",  # a path, though `usr` is a skill name
        "see https://example.com/tdd",
        "no slash here",
        "an unknown /nope skill",
        "inline code `/tdd` is not a reference",
        "```\nrun /tdd\n```",
        "a/tdd is a path fragment",
    ],
)
def test_text_that_is_not_a_skill_reference_gets_no_note(skills, text):
    assert sc.inline_skill_note(text) == ""


def test_a_trailing_punctuation_mark_does_not_hide_the_skill(skills):
    assert "/tdd" in sc.inline_skill_note("use /tdd.")
    assert "/tdd" in sc.inline_skill_note("(or /tdd)")


def test_at_most_five_skills_are_listed(skills, tmp_path, monkeypatch):
    names = [f"skill-{n}" for n in range(8)]
    commands = {f"/{n}": {"name": n, "description": f"d {n}"} for n in names}
    monkeypatch.setattr(sc, "get_interactive_skill_commands", lambda: commands)
    note = sc.inline_skill_note("go " + " ".join(f"/{n}" for n in names))
    assert note.count("\n- /") == 5


def test_a_long_description_is_cut(skills):
    skills["/tdd"]["description"] = "x" * 1000
    note = sc.inline_skill_note("use /tdd")
    assert "x" * 1000 not in note and "…" in note


def test_a_leading_skill_message_is_scanned_only_for_its_instruction(skills):
    message = sc.build_skill_invocation_message("/tdd", "then run /audit please")
    note = sc.inline_skill_note(message)
    assert "/audit" in note
    assert (
        "secret-skill" not in note
    )  # a `/name` inside the skill body is not the user's


def test_a_bare_leading_skill_message_gets_no_note(skills):
    message = sc.build_skill_invocation_message("/tdd", "")
    assert sc.inline_skill_note(message) == ""


def test_non_text_input_gets_no_note(skills):
    assert sc.inline_skill_note(None) == ""
    assert sc.inline_skill_note([{"type": "text", "text": "use /tdd"}]) == ""
