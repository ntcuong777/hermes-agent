"""`/memory approve|reject|show` by the number the list shows (M2).

A pending write is named by a random hex id (`d281178d`): hard to read off a phone and harder to type. The list now
numbers its rows, and `approve`, `reject` and `show` take that number, or nothing at all when exactly one write is
pending. With several pending, a bare `approve` asks which: it never guesses. `show` prints one write in full (the new
text, what it finds, the entry it is pinned to), since the list only carries a one-line summary.
"""

import os
import shutil
import tempfile
import time

import pytest

from hermes_cli.write_approval_commands import handle_pending_subcommand as run
from tools import write_approval as wa


@pytest.fixture
def hermes_home(monkeypatch):
    d = tempfile.mkdtemp(prefix="hermes_wa_sel_")
    home = os.path.join(d, ".hermes")
    os.makedirs(home)
    monkeypatch.setenv("HERMES_HOME", home)
    yield home
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def store(hermes_home):
    from tools.memory_tool import MemoryStore

    s = MemoryStore()
    s.load_from_disk()
    return s


def stage(content, summary=None, **extra):
    payload = {"action": "add", "target": "user", "content": content, **extra}
    record = wa.stage_write(
        "memory", payload, summary=summary or content[:40], origin="foreground"
    )
    time.sleep(0.02)  # records are ordered by created_at
    return record


def ids():
    return [r["id"] for r in wa.list_pending("memory")]


def test_the_list_numbers_its_rows_and_says_how_to_use_them(hermes_home):
    stage("the launch date is 3 May", "launch date")
    stage("prefers tabs over spaces", "tabs")
    first, second = ids()
    out = run(wa.MEMORY, ["pending"])
    assert f"1. {first}" in out and f"2. {second}" in out
    assert "/memory approve <n|id|all>" in out and "/memory show <n|id>" in out


def test_approve_by_number_applies_that_write_and_names_it(hermes_home, store):
    stage("first fact", "first")
    stage("second fact", "second")
    first, second = ids()
    out = run(wa.MEMORY, ["approve", "2"], memory_store=store)
    assert "Approved 1" in out and second in out and "second" in out
    assert ids() == [first]
    assert store.user_entries == ["second fact"]


def test_number_one_is_the_oldest(hermes_home, store):
    stage("old fact", "old")
    stage("new fact", "new")
    run(wa.MEMORY, ["approve", "1"], memory_store=store)
    assert store.user_entries == ["old fact"]


def test_a_number_past_the_list_changes_nothing_and_says_the_range(hermes_home, store):
    stage("a")
    stage("b")
    out = run(wa.MEMORY, ["approve", "5"], memory_store=store)
    assert "1-2" in out and "Approved" not in out
    assert len(ids()) == 2 and store.user_entries == []


def test_zero_is_not_a_position(hermes_home, store):
    stage("a")
    out = run(wa.MEMORY, ["approve", "0"], memory_store=store)
    assert "Approved" not in out and len(ids()) == 1


def test_a_bare_approve_takes_the_one_pending_write(hermes_home, store):
    stage("only fact", "only")
    out = run(wa.MEMORY, ["approve"], memory_store=store)
    assert "Approved 1" in out
    assert ids() == [] and store.user_entries == ["only fact"]


def test_a_bare_approve_with_several_pending_asks_which_and_applies_none(
    hermes_home, store
):
    stage("a")
    stage("b")
    out = run(wa.MEMORY, ["approve"], memory_store=store)
    assert "2 memory writes are pending" in out and "/memory approve <n|id|all>" in out
    assert len(ids()) == 2 and store.user_entries == []


def test_a_bare_approve_with_nothing_pending_says_so(hermes_home, store):
    assert (
        run(wa.MEMORY, ["approve"], memory_store=store) == "No pending memory writes."
    )


def test_an_exact_id_and_all_still_work(hermes_home, store):
    stage("a")
    stage("b")
    stage("c")
    first = ids()[0]
    assert "Approved 1" in run(wa.MEMORY, ["approve", first], memory_store=store)
    assert "Approved 2" in run(wa.MEMORY, ["approve", "all"], memory_store=store)
    assert ids() == []


def test_reject_by_number_and_bare(hermes_home):
    stage("a")
    stage("b")
    first, second = ids()
    assert second in run(wa.MEMORY, ["reject", "2"]) and ids() == [first]
    assert first in run(wa.MEMORY, ["reject"]) and ids() == []


def test_a_bare_reject_with_several_pending_asks_which(hermes_home):
    stage("a")
    stage("b")
    out = run(wa.MEMORY, ["reject"])
    assert "2 memory writes are pending" in out and len(ids()) == 2


def test_show_prints_the_whole_write_not_the_summary(hermes_home):
    long_text = "the release process: " + "step " * 60 + "done"
    stage(long_text, "release process")
    out = run(wa.MEMORY, ["show", "1"])
    assert long_text in out
    assert "add" in out and "user" in out and ids()[0] in out


def test_show_names_the_entry_a_replace_would_overwrite(hermes_home):
    stage(
        "new wording",
        "replace launch",
        action="replace",
        old_text="launch",
        matched_entry="the launch date is 3 May and the venue is Hanoi",
    )
    out = run(wa.MEMORY, ["show", "1"])
    assert "new wording" in out and "launch" in out
    assert "the launch date is 3 May and the venue is Hanoi" in out


def test_show_without_a_number_takes_the_one_pending_write_and_review_is_an_alias(
    hermes_home,
):
    stage("only fact", "only")
    assert "only fact" in run(wa.MEMORY, ["show"])
    assert "only fact" in run(wa.MEMORY, ["review", "1"])


def test_show_with_several_pending_asks_which(hermes_home):
    stage("a")
    stage("b")
    assert "2 memory writes are pending" in run(wa.MEMORY, ["show"])
