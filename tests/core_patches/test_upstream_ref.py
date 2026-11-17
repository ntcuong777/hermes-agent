"""scripts/fork/upstream-ref.sh: the latest upstream commit the fork merged, on synthetic repositories
that use each merge convention of the fork; it never fails."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "fork" / "upstream-ref.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                                reason="needs git and bash")


def git(repo, *args):
    out = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c",
                          "commit.gpgsign=false", *args], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def commit(repo, name, subject=None):
    (repo / name).write_text(name + "\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject or name)
    return git(repo, "rev-parse", "HEAD")


def ref(repo, *flags):
    out = subprocess.run(["bash", str(SCRIPT), *flags, str(repo)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stderr == ""
    return out.stdout.strip()


@pytest.fixture
def fork(tmp_path):
    """main = upstream: u1 u2 u3. personal branches at u1 and has a fork commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    shas = {name: commit(repo, name) for name in ("u1", "u2", "u3")}
    git(repo, "checkout", "-q", "-b", "personal", shas["u1"])
    commit(repo, "f1")
    return repo, shas


def test_merge_base_with_the_upstream_branch_is_the_latest_merged_commit(fork):
    repo, shas = fork
    git(repo, "merge", "-q", "--no-ff", shas["u2"], "-m", "Merge branch 'main' into personal")
    commit(repo, "f2")
    assert ref(repo) == shas["u2"]  # main has moved on to u3: only u2 was merged
    data = json.loads(ref(repo, "--json"))
    assert data["commit"] == shas["u2"] and data["via"].startswith("merge-base:") and len(data["date"]) == 10


@pytest.mark.parametrize("subject", [
    "Merge upstream main into personal", "Merge branch 'main' into personal",
    "Merge branch 'NousResearch:main' into personal", "Merge remote-tracking branch 'origin/main'",
    "Merge remote-tracking branch 'upstream/main'", "Merge origin/main into personal", "Merge upstream/main into personal",
])
def test_every_merge_subject_convention_works_without_any_upstream_ref(fork, subject):
    repo, shas = fork
    git(repo, "merge", "-q", "--no-ff", shas["u2"], "-m", subject)
    commit(repo, "f2")
    git(repo, "branch", "-q", "-D", "main")  # no ref to upstream at all: only the history can tell
    assert ref(repo) == shas["u2"]
    assert json.loads(ref(repo, "--json"))["via"] == "merge-subject"


def test_the_sync_pr_merge_is_followed_back_to_the_upstream_merge(fork):
    repo, shas = fork
    git(repo, "checkout", "-q", "-b", "sync/upstream")
    git(repo, "merge", "-q", "--no-ff", shas["u3"], "-m", "Merge upstream main into personal")
    commit(repo, "stamp", "chore: stamp upstream release 0.23.1")
    git(repo, "checkout", "-q", "personal")
    git(repo, "merge", "-q", "--no-ff", "sync/upstream", "-m", "Merge pull request #9 from owner/sync/upstream")
    git(repo, "branch", "-q", "-D", "main")
    git(repo, "branch", "-q", "-D", "sync/upstream")
    assert ref(repo) == shas["u3"]


def test_a_lagging_mirror_does_not_hide_a_newer_merge(fork):
    repo, shas = fork
    git(repo, "merge", "-q", "--no-ff", shas["u3"], "-m", "Merge upstream main into personal")
    git(repo, "branch", "-q", "-f", "main", shas["u1"])  # the local mirror is behind what was merged
    assert ref(repo) == shas["u3"]


def test_it_never_fails(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ref(plain) == "unknown"
    assert json.loads(ref(plain, "--json")) == {"commit": "unknown", "release": "unknown", "date": "unknown",
                                                 "via": "unknown"}
    (plain / "hermes-upstream-commit").write_text("a" * 40 + "\n", encoding="utf-8")
    (plain / "hermes-upstream-release").write_text("0.23.0\n", encoding="utf-8")
    data = json.loads(ref(plain, "--json"))
    assert data["commit"] == "a" * 40 and data["release"] == "0.23.0" and data["via"] == "stamp-file"
    assert subprocess.run(["bash", str(SCRIPT), "--no-such-flag", str(tmp_path / "missing")],
                          capture_output=True, text=True).returncode == 0


def test_stamp_writes_the_commit_file_and_reads_the_release(fork):
    repo, shas = fork
    git(repo, "merge", "-q", "--no-ff", shas["u2"], "-m", "Merge branch 'main' into personal")
    (repo / "hermes-upstream-release").write_text("0.23.0\n", encoding="utf-8")
    assert ref(repo, "--stamp") == shas["u2"]
    assert (repo / "hermes-upstream-commit").read_text(encoding="utf-8").strip() == shas["u2"]
    assert json.loads(ref(repo, "--json"))["release"] == "0.23.0"


def test_the_stamp_of_this_checkout_matches_its_history_when_the_history_is_there():
    stamp = ROOT / "hermes-upstream-commit"
    if not (ROOT / ".git").exists() or subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-shallow-repository"],
                                                       capture_output=True, text=True).stdout.strip() != "false":
        pytest.skip("needs a full git clone")
    expected = ref(ROOT)
    if expected == "unknown":
        pytest.skip("this clone has no upstream history to compare with")
    assert stamp.is_file(), "run scripts/fork/upstream-ref.sh --stamp and commit hermes-upstream-commit"
    assert stamp.read_text(encoding="utf-8").strip() == expected, (
        "hermes-upstream-commit is stale: run scripts/fork/upstream-ref.sh --stamp and commit it")
