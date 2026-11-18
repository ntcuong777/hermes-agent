"""Provenance of core patches: record it, diff it, brief an agent about it (fork-owned).

A patch is only safe to keep (and to copy upstream code into) if a later reader can tell exactly what
changed upstream. ``record`` writes the provenance record and the snapshot of the ORIGINAL upstream
source into the plugin's ``core_patches.lock.json`` (schema v2, see ``core_patches_ast``); the
functions here read it back for ``hermes plugins patches --upstream-diff / --agent-brief / --report``.

Every function raises only ``ValueError`` subclasses with a message meant for the terminal; the
callers (``core_patches_cmd``) turn them into exit code 2.
"""

from __future__ import annotations

import difflib
import inspect
import sys
import textwrap
from pathlib import Path
from typing import Any, Iterable, Optional

from hermes_cli import core_patches_report as report
from hermes_cli.core_patches_ast import (
    DEFAULT_SOURCE_ROOT, LOCK_FILE, CorePatchFileError, CorePatchTargetError, _fenced, _git, lock_diff, read_lock,
    snapshot_text, target_file_rel, target_source, write_lock_entry,
)

FORK_RULES = (
    "This is a personal fork: never open an upstream pull request or issue, never push or merge on your own.",
    "Work in a git worktree under `<fork>/.worktrees/<branch>` (never the live checkout); branch from `personal`.",
    "Commit with `git commit --no-verify`; no Co-Authored-By or AI attribution lines; no em-dashes or emoji.",
    "Run only focused tests, `HERMES_PYTHON=<fork>/.venv/bin/python scripts/run_tests.sh -j 3 <explicit files>`; never the full suite.",
    "Never read or write `.env*` files; never use rm/mv/rmdir/dd (use `git rm`).",
)


def _find(records: Iterable[Any], name: str) -> Any:
    chosen = [r for r in records if name in (r.spec.name, r.spec.key)]
    if not chosen:
        raise CorePatchTargetError(f"no core patch named {name!r} (is its plugin loaded and enabled? "
                                   "see hermes plugins patches)")
    if len(chosen) > 1:
        raise CorePatchTargetError(f"{name!r} is ambiguous: " + ", ".join(r.spec.key for r in chosen))
    return chosen[0]


def find_record(name: str) -> Any:
    """The registry record named ``name`` (a patch name or ``plugin:name``) after a plugin discovery."""
    from hermes_cli import core_patches
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    return _find(core_patches.records(), name)


def _one_line(reason: str) -> str:
    text = (reason or "").strip()
    if not text or "\n" in text or len(text) > 240:
        raise ValueError("--reason must be one non-empty line of at most 240 characters (the bug or purpose)")
    return text


def _plugin_dir(record: Any) -> Path:
    if not record.spec.plugin_dir:
        raise CorePatchTargetError(f"{record.spec.key} has no plugin directory (it was not registered by a plugin "
                                   "that has a manifest path)")
    return Path(record.spec.plugin_dir)


def patch_function(record: Any) -> tuple[str, str]:
    """``(source text, "path/in/plugin.py:qualname")`` of the plugin function behind the patch: the wrap
    factory, or the copied replacement body. ``("", "")`` when it cannot be located."""
    try:
        fn = inspect.unwrap(record.spec.fn)
        source = textwrap.dedent(inspect.getsource(fn))
        file = Path(inspect.getsourcefile(fn) or "").resolve()
        rel = file.relative_to(_plugin_dir(record).resolve()).as_posix()
        return source, f"{rel}:{getattr(fn, '__qualname__', getattr(fn, '__name__', '?'))}"
    except (OSError, TypeError, ValueError, CorePatchTargetError):
        return "", ""


def record(record_: Any, reason: str, *, source_root: Optional[Path] = None) -> dict:
    """Write (or refresh) the provenance record and the upstream snapshot of one patch from the CURRENT core
    source. Run it right after a human or agent reviewed a drift and updated the patch."""
    reason = _one_line(reason)
    spec, root = record_.spec, Path(source_root) if source_root is not None else DEFAULT_SOURCE_ROOT
    plugin_dir = _plugin_dir(record_)
    current = target_source(spec.target, source_root)
    rel = target_file_rel(current, source_root)
    who = report._upstream_ref(root)
    upstream_commit = who["commit"]
    fork_head = _git(root, "rev-parse", "HEAD") or report._fork_head()
    blob_at_upstream = _git(root, "rev-parse", f"{upstream_commit}:{rel}") if upstream_commit != "unknown" else ""
    fn_source, fn_ref = patch_function(record_)
    provenance = {
        "plugin": spec.plugin_id, "kind": spec.kind, "reason": reason, "fork_head": fork_head,
        "fork_dirty": bool(_git(root, "status", "--porcelain", "--", rel)),
        "upstream_ref": who, "target_file_blob_sha": blob_at_upstream,
        "fork_file_blob_sha": _git(root, "rev-parse", f"HEAD:{rel}"),
        "patch_fn": fn_ref, "copy_of": fn_ref if spec.kind == "replace" and fn_ref else None,
    }
    sha = write_lock_entry(plugin_dir, spec.name, spec.target, source_root, provenance=provenance)
    return {"name": spec.name, "plugin_dir": str(plugin_dir), "ast_sha": sha, "target_file": rel,
            "expect_ast_sha_in_plugin": spec.expect_ast_sha, "stale_expectation": spec.expect_ast_sha != sha,
            "fork_dirty": provenance["fork_dirty"], "upstream_ref": who, "patch_fn": fn_ref}


def _entry(record_: Any) -> dict:
    entry = read_lock(_plugin_dir(record_)).get(record_.spec.name)
    if not isinstance(entry, dict):
        raise CorePatchFileError(f"{record_.spec.key}: no entry in {_plugin_dir(record_) / LOCK_FILE}; record it with "
                                 f"hermes plugins patches --record {record_.spec.name} --reason \"...\"")
    return entry


def _udiff(old: str, new: str, old_label: str, new_label: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=old_label, tofile=new_label))


def upstream_diff(record_: Any, source_root: Optional[Path] = None) -> str:
    """Two unified diffs: the recorded upstream source of the target against the CURRENT core source (what
    upstream changed), and the plugin's copy against the recorded source (what the copy changed)."""
    entry, plugin_dir = _entry(record_), _plugin_dir(record_)
    out = [f"# {record_.spec.key}: recorded upstream snapshot -> current core ({record_.spec.target})\n",
           lock_diff(entry, source_root, plugin_dir) or "(no change)\n"]
    fn_source, fn_ref = patch_function(record_)
    old = snapshot_text(plugin_dir, entry) or ""
    if record_.spec.kind == "replace" and fn_source:
        out += [f"\n# {record_.spec.key}: recorded upstream snapshot -> the plugin's copy ({fn_ref})\n",
                _udiff(textwrap.dedent(old), fn_source, "recorded upstream", f"plugin copy {fn_ref}") or "(identical)\n"]
    elif fn_source:
        out.append(f"\n# {record_.spec.key} is a wrap: there is no copy to compare; the wrapper is {fn_ref}\n")
    return "".join(out)


def _python_for_tests() -> str:
    venv = DEFAULT_SOURCE_ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def agent_brief(record_: Any, source_root: Optional[Path] = None) -> str:
    """A self-contained markdown brief a later agent can act on without prior context."""
    spec, plugin_dir = record_.spec, _plugin_dir(record_)
    root = Path(source_root) if source_root is not None else DEFAULT_SOURCE_ROOT
    entry = _entry(record_)
    upstream = entry.get("upstream_ref") if isinstance(entry.get("upstream_ref"), dict) else {}
    commit = str(upstream.get("commit") or "unknown")
    old = snapshot_text(plugin_dir, entry) or "(snapshot missing)"
    try:
        current = target_source(spec.target, source_root)
        new, current_sha = current.source, current.sha
        rel = target_file_rel(current, source_root)
    except CorePatchTargetError as exc:
        new, current_sha, rel = f"(unavailable: {exc})", "", str(entry.get("target_file") or "")
    history = (_git(root, "log", "--oneline", "-n", "20", f"{commit}..HEAD", "--", rel)
               if commit not in ("", "unknown") and rel else "")
    fn_source, fn_ref = patch_function(record_)
    py, name = _python_for_tests(), spec.name
    pins = root / ".github" / "core-patch-pins.json"
    lines = [
        f"# Core patch brief: {spec.key}", "",
        f"Self-contained: you need no earlier context. Generated by `hermes plugins patches --agent-brief {name}`.", "",
        "## What it is", "",
        f"- patch: `{spec.key}` ({spec.kind}{', critical' if record_.critical else ''}), plugin directory `{plugin_dir}`",
        f"- purpose (the bug or reason): {entry.get('reason') or '(none recorded: ask the user, then use --record --reason)'}",
        f"- status in this process: `{record_.status}`" + (f" ({record_.detail})" if record_.detail else "")
        + (" DRIFTED: the plugin's patch is RUNNING against code that changed upstream (nothing failed); re-sync it "
           "with the steps below, or set `on_drift: skip` (registration) / `core_patches.on_drift: skip` (config.yaml) "
           "to run upstream's code instead" if record_.drifted else ""),
        f"- target: `{spec.target}` in `{rel}` (repo relative, in the fork checkout `{root}`)",
        f"- fingerprints: plugin expects `{spec.expect_ast_sha}`; lock recorded `{entry.get('ast_sha')}`; "
        f"current core `{current_sha or 'unknown'}`",
        f"- recorded against upstream commit `{commit}` (release `{upstream.get('release', 'unknown')}`, "
        f"date `{upstream.get('date', 'unknown')}`), fork head `{entry.get('fork_head', 'unknown')}`, "
        f"authored `{entry.get('authored_at', 'unknown')}`, file blob at that upstream commit "
        f"`{entry.get('target_file_blob_sha') or 'unknown'}`", ""]
    if history:
        lines += ["## Commits that touched the file since the recorded upstream commit", "", _fenced(history, "")]
    lines += ["## Old upstream source (recorded snapshot)", "", _fenced(old, "python", 400),
              "## Current core source", "", _fenced(new, "python", 400),
              "## Diff old -> current", "",
              _fenced(_udiff(old, new, "recorded", "current") or "(no change)", "diff", 400)]
    if fn_source:
        lines += [f"## The plugin's {'copied replacement' if spec.kind == 'replace' else 'wrapper factory'} "
                  f"(`{fn_ref}`)", "", _fenced(fn_source, "python", 400)]
        if spec.kind == "replace":
            lines += ["## Diff: recorded upstream -> the plugin's copy (what the copy changed on purpose)", "",
                      _fenced(_udiff(textwrap.dedent(old), fn_source, "recorded", "plugin copy") or "(identical)",
                              "diff", 400)]
    lines += [
        "## What to do", "",
        "Paths are those of the install that generated this brief. If they are under /nix/store, edit the fork "
        "checkout (`fork/hermes-agent` in the nix-darwin repo, in a worktree) instead.", "",
        "1. Read the diff old -> current. Decide whether the patch is still needed (upstream may have fixed it).",
        f"2. Re-sync the patch in `{plugin_dir / fn_ref.split(':')[0] if fn_ref else plugin_dir}`: "
        + ("copy the new upstream function again and re-apply the deliberate changes shown in the last diff."
           if spec.kind == "replace" else "adapt the wrapper to the new function (signature, names it relies on)."),
        f"3. Put the new fingerprint in `expect_ast_sha`: `hermes plugins patches --sha {spec.target}` (stdlib: "
        f"`python3 -I {root}/hermes_cli/core_patches_ast.py --sha {spec.target} --source-root {root}`).",
        f"4. Refresh the provenance (lock and snapshot): `hermes plugins patches --record {name} --reason "
        f"\"{entry.get('reason') or '<one line: the bug or purpose>'}\"`.",
        f"5. Refresh the fork pins and commit them on the fork: `python3 -I {root}/hermes_cli/core_patches_ast.py "
        f"--write-pins {plugin_dir.parent} {pins}`.",
        f"6. Run the plugin's tests: `cd {plugin_dir} && {py} -m pytest tests -q -p no:cacheprovider`.",
        f"7. Verify: `hermes plugins patches --strict` exits 0 and shows `{spec.key}` active; `hermes plugins patches "
        f"--verify-pins {pins} --plugins-root {plugin_dir.parent}` prints no drift.",
        "8. Commit (nix-darwin repo: plugin, lock, snapshot; fork: pins) with `git commit --no-verify`.", "",
        "## Rules of this fork", ""] + [f"- {rule}" for rule in FORK_RULES] + [""]
    return "\n".join(lines)


def report_markdown(records: Iterable[Any], source_root: Optional[Path] = None) -> str:
    """The markdown report of the live registry (a PR or issue body): a table and, for every patch that is
    not active, the diff from the recorded upstream source and the pointer to its agent brief."""
    items = list(records)
    failures = report._failures()
    who = report.identity()
    lines = [f"## Core patches ({len(items)})", "",
             f"fork head `{who['fork_head']}`, upstream `{who['upstream_ref']['commit']}` "
             f"(release {who['upstream_ref']['release']})", "",
             "| patch | target | status | expected | current | upstream commit | reason |", "|---|---|---|---|---|---|---|"]
    details = []
    for item in items:
        row = report.row_of(item, failures)
        lines.append(f"| `{row['plugin']}:{row['name']}` | `{row['target']}` | {row['status']} | "
                     f"`{row['expect_ast_sha'][:12]}` | `{row['current_sha'][:12] or '-'}` | "
                     f"`{row['upstream_commit'][:12]}` | {(row['reason'] or '-').replace('|', '/')} |")
        if item.status == "active" and not item.drifted:
            continue
        body = f"{item.detail}\n"
        try:
            text = lock_diff(_entry(item), source_root, _plugin_dir(item))
            body += _fenced(text, "diff") if text else "(no source change since the recorded snapshot)\n"
        except (CorePatchTargetError, OSError) as exc:
            body += f"(no diff: {exc})\n"
        details.append(f"### {item.spec.key} ({item.status})\n\n{body}\nBrief: "
                       f"`hermes plugins patches --agent-brief {item.spec.name}`\n")
    return "\n".join(lines) + "\n\n" + "\n".join(details)


def lock_problems(records: Iterable[Any]) -> list[str]:
    """One line per registered patch whose plugin lock has no complete provenance record."""
    from hermes_cli.core_patches_ast import provenance_problems

    out: list[str] = []
    for item in records:
        if not item.spec.plugin_dir:
            continue
        plugin_dir = Path(item.spec.plugin_dir)
        try:
            entry = read_lock(plugin_dir).get(item.spec.name)
        except CorePatchFileError as exc:
            out.append(f"PROVENANCE {item.spec.key}: {exc}")
            continue
        out += (provenance_problems(item.spec.key, entry, plugin_dir) if entry is not None
                else [f"PROVENANCE {item.spec.key}: no entry in {LOCK_FILE}"])
    return out
