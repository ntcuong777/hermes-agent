"""``hermes plugins patches`` and the ``hermes doctor`` row for the core-patch registry (fork-owned)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from hermes_cli.doctor_report import Finding, _section, check_ok, check_warn, doctor_check


def add_patches_parser(plugins_subparsers: Any) -> None:
    """Attach ``hermes plugins patches`` (called from hermes_cli/subcommands/plugins.py)."""
    parser = plugins_subparsers.add_parser(
        "patches", help="Core patches plugins registered: status, drift, fingerprints",
        description="List core patches (active, skipped-drift, skipped-no-grant, skipped-error), print a "
                    "target's AST fingerprint, show what changed since a lock, or record a lock entry.")
    parser.add_argument("--json", action="store_true", dest="patches_json", help="Machine-readable listing")
    parser.add_argument("--sha", metavar="TARGET", help="Print the fingerprint of pkg.mod:function or pkg.mod:Class.method")
    parser.add_argument("--diff", nargs="?", const="", default=None, metavar="NAME",
                        help="Diff each patch target (or NAME) against its core_patches.lock.json source")
    parser.add_argument("--lock", metavar="PLUGIN_DIR", help="Write/refresh a lock entry (needs --name and --target)")
    parser.add_argument("--name", help="Patch name for --lock")
    parser.add_argument("--target", help="Target for --lock")


def _rows() -> list[dict[str, Any]]:
    from hermes_cli import core_patches
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    return [
        {"plugin": r.spec.plugin_id, "name": r.spec.name, "target": r.spec.target, "kind": r.spec.kind,
         "order": r.spec.order, "critical": r.spec.critical, "status": r.status, "detail": r.detail}
        for r in core_patches.records()
    ]


def _print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No core patches registered.")
        return
    for row in rows:
        flag = " critical" if row["critical"] else ""
        detail = f"  {row['detail']}" if row["detail"] else ""
        print(f"{row['status']:<17} {row['plugin']}:{row['name']}  {row['kind']}{flag} order={row['order']}  "
              f"{row['target']}{detail}")


def _diff(name: str) -> int:
    from hermes_cli import core_patches
    from hermes_cli.core_patches_ast import CorePatchTargetError, LOCK_FILE, lock_diff, read_lock
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    chosen = [r for r in core_patches.records() if not name or name in (r.spec.name, r.spec.key)]
    if not chosen:
        print(f"No core patch named {name!r}." if name else "No core patches registered.")
        return 1 if name else 0
    changed = 0
    for record in chosen:
        entry = read_lock(Path(record.spec.plugin_dir)).get(record.spec.name) if record.spec.plugin_dir else None
        if not entry:
            print(f"{record.spec.key}: no entry in {LOCK_FILE}")
            continue
        try:
            text = lock_diff(entry)
        except CorePatchTargetError as exc:
            print(f"{record.spec.key}: {exc}")
            changed += 1
            continue
        if text:
            changed += 1
            print(f"{record.spec.key}: {record.spec.target} changed since the lock:")
            print(text, end="" if text.endswith("\n") else "\n")
        else:
            print(f"{record.spec.key}: no change since the lock")
    return 1 if changed else 0


def cmd_patches(args: Any) -> None:
    """Dispatch ``hermes plugins patches``; exits non-zero via SystemExit on failure or drift."""
    from hermes_cli.core_patches_ast import CorePatchTargetError, core_patch_sha, write_lock_entry

    code = 0
    try:
        if getattr(args, "sha", None):
            print(core_patch_sha(args.sha))
        elif getattr(args, "lock", None):
            if not (args.name and args.target):
                print("--lock needs --name and --target", file=sys.stderr)
                code = 2
            else:
                print(f"{args.name}: {write_lock_entry(Path(args.lock), args.name, args.target)}")
        elif getattr(args, "diff", None) is not None:
            code = _diff(args.diff)
        elif getattr(args, "patches_json", False):
            print(json.dumps(_rows(), indent=2))
        else:
            _print_table(_rows())
    except CorePatchTargetError as exc:
        print(f"hermes plugins patches: {exc}", file=sys.stderr)
        code = 2
    if code:
        raise SystemExit(code)


@doctor_check("")  # best-effort: a broken registry must never break doctor
def _check_core_patches(should_fix: bool, f: Finding) -> None:
    from hermes_cli import core_patches
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    current = core_patches.records()
    if not current:
        return
    _section("Core Patches")
    for record in current:
        label = f"{record.spec.key} -> {record.spec.target}"
        if record.status == core_patches.ACTIVE:
            check_ok(label, "(active)")
            continue
        check_warn(label, f"({record.status}: {record.detail})")
        f.manual_issues.append(
            f"Core patch {record.spec.key} on {record.spec.target} is {record.status}: {record.detail}. "
            f"Run `hermes plugins patches --diff {record.spec.name}`.")
