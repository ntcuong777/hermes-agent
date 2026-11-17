"""``hermes plugins patches`` and the ``hermes doctor`` row for the core-patch registry (fork-owned).

Only the standard library at module level: ``build_plugins_parser`` imports this for every
``hermes`` invocation, so an upstream rename in ``doctor_report`` must not break the CLI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

FOOTER = ("(status from this command's own plugin discovery in a fresh process, not the running gateway: "
          "see the gateway log line 'core patches (gateway)')")


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
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 when any patch is skipped-drift or skipped-error")
    parser.add_argument("--verify-pins", metavar="PINS_FILE",
                        help="Verify a pins file (.github/core-patch-pins.json) against this checkout")
    parser.add_argument("--plugins-root", metavar="DIR",
                        help="With --verify-pins: also check each plugin's expect_ast_sha against its lock")


def _rows() -> list[dict[str, Any]]:
    from hermes_cli import core_patches
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    return [
        {"plugin": r.spec.plugin_id, "name": r.spec.name, "target": r.spec.target, "kind": r.spec.kind,
         "order": r.spec.order, "critical": r.critical, "status": r.status, "detail": r.detail}
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
    print(FOOTER)


def _strict_code(rows: list[dict[str, Any]], strict: bool) -> int:
    return 1 if strict and any(row["status"] in ("skipped-drift", "skipped-error") for row in rows) else 0


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
        elif getattr(args, "verify_pins", None):
            from hermes_cli.core_patches_ast import run

            code = run(SimpleNamespace(sha=None, write_pins=None, verify_locks=None, verify_pins=args.verify_pins,
                                       plugins_root=Path(args.plugins_root) if args.plugins_root else None,
                                       source_root=None))
        elif getattr(args, "diff", None) is not None:
            code = _diff(args.diff)
        elif getattr(args, "patches_json", False):
            rows = _rows()
            print(json.dumps(rows, indent=2))
            code = _strict_code(rows, getattr(args, "strict", False))
        else:
            rows = _rows()
            _print_table(rows)
            code = _strict_code(rows, getattr(args, "strict", False))
    except CorePatchTargetError as exc:
        print(f"hermes plugins patches: {exc}", file=sys.stderr)
        code = 2
    if code:
        raise SystemExit(code)


def _check_core_patches(should_fix: bool) -> Any:
    """The ``hermes doctor`` row (best-effort: a broken registry must never break doctor)."""
    from hermes_cli.doctor_report import doctor_check

    return doctor_check("")(_doctor_rows)(should_fix)


def _doctor_rows(should_fix: bool, f: Any) -> None:
    from hermes_cli import core_patches
    from hermes_cli.doctor_report import _section, check_ok, check_warn
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
