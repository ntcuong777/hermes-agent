"""scripts/fork/tui-slot-check: the region rule, record, verify, pins, report and the agent brief (spec-06b)."""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "fork" / "tui-slot-check"
VECTORS = REPO / "ui-tui" / "src" / "sdk" / "ext" / "slotRegion.vectors.json"

_loader = importlib.machinery.SourceFileLoader("tui_slot_check", str(SCRIPT))
_spec = importlib.util.spec_from_loader("tui_slot_check", _loader)
tsc = importlib.util.module_from_spec(_spec)
_loader.exec_module(tsc)

UPSTREAM = "import { a } from './a.js'\n\n/** The switcher. */\nexport function Switcher() {\n  return 1\n}\n\nconst other = 1\n"
FORK = UPSTREAM.replace("return 1", "return 2")
SLOTS = {"version": 1, "slots": {"sessionSwitcher": {"file": "ui-tui/src/components/sw.tsx", "symbols": ["Switcher"], "props": 1, "kind": "component", "host": [], "summary": "x"}}}


def git(root, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null"}
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


@pytest.fixture()
def fork(tmp_path):
    """A fork checkout whose HEAD is the upstream commit U (the component as upstream has it)."""
    root = tmp_path / "fork"
    (root / "ui-tui/src/components").mkdir(parents=True)
    (root / "ui-tui/src/sdk/ext").mkdir(parents=True)
    (root / "ui-tui/src/components/sw.tsx").write_text(UPSTREAM)
    (root / "ui-tui/src/sdk/ext/slots.json").write_text(json.dumps(SLOTS))
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "upstream")
    commit = git(root, "rev-parse", "HEAD")
    (root / "hermes-upstream-commit").write_text(commit + "\n")
    return root, commit


@pytest.fixture()
def plugin(tmp_path):
    root = tmp_path / "plugins" / "demo"
    root.mkdir(parents=True)
    return root


def record(fork_root, plugin_dir, reason="the fork's switcher"):
    return tsc.main(["--record", "sessionSwitcher", "--plugin-dir", str(plugin_dir), "--fork-root", str(fork_root), "--reason", reason,
                     "--extension", "switcher", "--patch-fn", "tui/slots/sw.tsx:Switcher", "--copy-of", "sw.tsx:Switcher"])


def test_the_region_rule_matches_the_shared_vectors():
    for vector in json.loads(VECTORS.read_text()):
        if vector.get("error"):
            with pytest.raises(KeyError):
                tsc.region_hash(vector["source"], vector["symbols"])
        else:
            assert tsc.region_hash(vector["source"], vector["symbols"]) == vector["sha256"], vector["name"]


def test_record_writes_lock_and_snapshot_with_the_provenance_fields(fork, plugin, capsys):
    root, commit = fork
    assert record(root, plugin) == 0
    assert "recorded" in capsys.readouterr().out
    lock = json.loads((plugin / "tui_slots.lock.json").read_text())
    entry = lock["slots"]["sessionSwitcher"]
    assert lock["version"] == 2
    assert entry["kind"] == "tui-slot" and entry["target"] == "sessionSwitcher@ui-tui/src/components/sw.tsx"
    assert entry["upstream_snapshot"] == {"algo": "sha256-lf-region", "sha": tsc.region_hash(UPSTREAM, ["Switcher"])}
    assert entry["upstream_ref"]["commit"] == commit and entry["reason"] == "the fork's switcher"
    assert entry["symbols"] == ["Switcher"] and entry["props"] == 1
    assert entry["patch_fn"] == "tui/slots/sw.tsx:Switcher" and entry["copy_of"] == "sw.tsx:Switcher"
    assert entry["target_file_blob_sha"] and entry["fork_head"] == commit
    snapshot = (plugin / entry["snapshot"]).read_text()
    assert snapshot == "/** The switcher. */\nexport function Switcher() {\n  return 1\n}\n"


def test_record_refuses_without_a_reason_an_unknown_slot_or_an_unknown_upstream(fork, plugin):
    root, _ = fork
    with pytest.raises(SystemExit, match="--reason is required"):
        record(root, plugin, reason=" ")
    with pytest.raises(SystemExit, match="not in"):
        tsc.main(["--record", "nope", "--plugin-dir", str(plugin), "--fork-root", str(root), "--reason", "x"])
    (root / "hermes-upstream-commit").write_text("unknown\n")
    with pytest.raises(SystemExit, match="no upstream commit"):
        record(root, plugin)


def test_record_warns_when_the_checkout_is_not_back_to_upstream_yet(fork, plugin, capsys):
    root, _ = fork
    (root / "ui-tui/src/components/sw.tsx").write_text(FORK)
    record(root, plugin)
    assert "core file is not back to upstream yet" in capsys.readouterr().out


def test_verify_locks_is_clean_when_the_checkout_is_upstream_and_fails_on_drift_with_a_diff(fork, plugin, capsys):
    root, _ = fork
    record(root, plugin)
    capsys.readouterr()
    assert tsc.main(["--verify-locks", str(plugin.parent), "--fork-root", str(root)]) == 0
    (root / "ui-tui/src/components/sw.tsx").write_text(FORK)
    assert tsc.main(["--verify-locks", str(plugin.parent), "--fork-root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "| sessionSwitcher | drift |" in out
    assert "-  return 1" in out and "+  return 2" in out


def test_an_unrelated_edit_of_the_same_file_is_not_drift(fork, plugin):
    root, _ = fork
    record(root, plugin)
    (root / "ui-tui/src/components/sw.tsx").write_text(UPSTREAM.replace("const other = 1", "const other = 99"))
    assert tsc.main(["--verify-locks", str(plugin.parent), "--fork-root", str(root)]) == 0


def test_an_empty_symbols_list_is_a_present_field_whole_file_contract(fork, plugin, capsys):
    # symbols: [] is the documented whole-file slot (sha256-lf); the REQUIRED gate must treat it as present,
    # and the whole-file hash must verify. The lock is rewritten coherently: empty symbols and the whole-file
    # hash of the checkout's file, so the only question the run answers is the REQUIRED-gate one.
    root, _ = fork
    record(root, plugin)
    path = plugin / "tui_slots.lock.json"
    lock = json.loads(path.read_text())
    current = (root / "ui-tui/src/components/sw.tsx").read_text()
    lock["slots"]["sessionSwitcher"]["symbols"] = []
    lock["slots"]["sessionSwitcher"]["upstream_snapshot"]["sha"] = tsc.region_hash(current, [])
    path.write_text(json.dumps(lock))
    assert tsc.main(["--verify-locks", str(plugin.parent), "--fork-root", str(root)]) == 0
    assert "missing field 'symbols'" not in capsys.readouterr().out


@pytest.mark.parametrize("damage,needle", [
    (lambda e: e.pop("reason"), "missing field 'reason'"),
    (lambda e: e["upstream_ref"].update(commit=""), "missing upstream_ref.commit"),
    (lambda e: e.update(snapshot_sha256="0" * 64), "does not match snapshot_sha256"),
    (lambda e: e.update(snapshot="upstream-source/gone.txt"), "snapshot file upstream-source/gone.txt not found"),
])
def test_missing_provenance_fails_the_check(fork, plugin, capsys, damage, needle):
    root, _ = fork
    record(root, plugin)
    path = plugin / "tui_slots.lock.json"
    lock = json.loads(path.read_text())
    damage(lock["slots"]["sessionSwitcher"])
    path.write_text(json.dumps(lock))
    assert tsc.main(["--verify-locks", str(plugin.parent), "--fork-root", str(root)]) == 1
    assert needle in capsys.readouterr().out


def test_a_removed_symbol_or_file_is_reported_not_raised(fork, plugin, capsys):
    root, _ = fork
    record(root, plugin)
    (root / "ui-tui/src/components/sw.tsx").write_text("const other = 1\n")
    assert tsc.main(["--verify-locks", str(plugin.parent), "--fork-root", str(root)]) == 1
    assert "symbol 'Switcher' not found" in capsys.readouterr().out
    (root / "ui-tui/src/components/sw.tsx").unlink()
    assert tsc.main(["--verify-locks", str(plugin.parent), "--fork-root", str(root)]) == 1
    assert "not found in the checkout" in capsys.readouterr().out


def test_pins_roundtrip_and_fail_on_drift(fork, plugin, tmp_path, capsys):
    root, _ = fork
    record(root, plugin)
    pins = tmp_path / "pins.json"
    assert tsc.main(["--write-pins", str(plugin.parent), str(pins)]) == 0
    data = json.loads(pins.read_text())
    assert data["slots"]["sessionSwitcher"]["sha"] == tsc.region_hash(UPSTREAM, ["Switcher"])
    capsys.readouterr()
    assert tsc.main(["--verify-pins", str(pins), "--fork-root", str(root)]) == 0
    (root / "ui-tui/src/components/sw.tsx").write_text(FORK)
    assert tsc.main(["--verify-pins", str(pins), "--fork-root", str(root)]) == 1
    assert "| sessionSwitcher | drift |" in capsys.readouterr().out


def test_report_always_exits_zero(fork, plugin, capsys):
    root, _ = fork
    record(root, plugin)
    (root / "ui-tui/src/components/sw.tsx").write_text(FORK)
    assert tsc.main(["--report", str(plugin.parent), "--fork-root", str(root)]) == 0
    assert "drift" in capsys.readouterr().out


def test_the_agent_brief_has_old_and_new_source_the_diff_the_steps_and_the_commands(fork, plugin, capsys):
    root, commit = fork
    (plugin / "tui/slots").mkdir(parents=True)
    (plugin / "tui/slots/sw.tsx").write_text("// the copy\n")
    record(root, plugin)
    (root / "ui-tui/src/components/sw.tsx").write_text(FORK)
    capsys.readouterr()
    assert tsc.main(["--agent-brief", "sessionSwitcher", "--plugins-root", str(plugin.parent), "--fork-root", str(root)]) == 0
    brief = capsys.readouterr().out
    for needle in [
        "# Agent brief: TUI slot sessionSwitcher", "status: drift", "purpose: the fork's switcher", f"recorded against upstream {commit}",
        "## OLD upstream region (snapshot)", "## NEW upstream region (the checkout now)", "-  return 1", "+  return 2",
        "tui-slot-build.mjs", "tui-slot-check --record sessionSwitcher", "git merge-file", "--write-pins", "If upstream fixed the bug",
    ]:
        assert needle in brief, needle
    assert tsc.main(["--agent-brief", "nope", "--plugins-root", str(plugin.parent), "--fork-root", str(root)]) == 0


def test_hash_prints_the_region_hash(tmp_path, capsys):
    f = tmp_path / "x.ts"
    f.write_text(UPSTREAM)
    assert tsc.main(["--hash", str(f), "Switcher"]) == 0
    assert capsys.readouterr().out.strip() == tsc.region_hash(UPSTREAM, ["Switcher"])
