"""``hermes plugins patches`` and the ``hermes doctor`` row for the core-patch registry (fork-owned).

Only the standard library at module level: ``build_plugins_parser`` imports this for every
``hermes`` invocation, so an upstream rename in ``doctor_report`` must not break the CLI. The report
modes (``--report``, ``--upstream-diff``, ``--agent-brief``) print and exit 0; only ``--strict``,
``--verify-pins`` and an unusable input exit non-zero.
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
        description="List core patches (active, skipped-drift, skipped-no-grant, skipped-error, "
                    "disabled-after-errors), print a target's AST fingerprint, show what changed upstream since "
                    "the recorded snapshot, record provenance, or brief an agent about a drifted patch.")
    parser.add_argument("--json", action="store_true", dest="patches_json", help="Machine-readable listing")
    parser.add_argument("--sha", metavar="TARGET", help="Print the fingerprint of pkg.mod:function or pkg.mod:Class.method")
    parser.add_argument("--diff", nargs="?", const="", default=None, metavar="NAME",
                        help="Diff each patch target (or NAME) against its recorded upstream snapshot")
    parser.add_argument("--lock", metavar="PLUGIN_DIR",
                        help="Write/refresh a bare lock entry (needs --name and --target; prefer --record)")
    parser.add_argument("--record", metavar="NAME",
                        help="Write the provenance record and upstream snapshot of patch NAME from the CURRENT core "
                             "source (needs --reason); run it right after reviewing a drift and updating the patch")
    parser.add_argument("--reason", help="With --record: one line, the bug or purpose of the patch")
    parser.add_argument("--report", action="store_true", help="Markdown report for a PR or issue body")
    parser.add_argument("--upstream-diff", metavar="NAME",
                        help="Diff the recorded upstream snapshot against the current core target, and against "
                             "the plugin's copy")
    parser.add_argument("--agent-brief", metavar="NAME",
                        help="A self-contained markdown brief for an agent that has to re-sync patch NAME")
    parser.add_argument("--migrate-lock", metavar="PLUGIN_DIR", help="Rewrite a schema v1 lock as v2 in place")
    parser.add_argument("--name", help="Patch name for --lock")
    parser.add_argument("--target", help="Target for --lock")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 when any patch is skipped-drift or skipped-error")
    parser.add_argument("--verify-pins", metavar="PINS_FILE",
                        help="Verify a pins file (.github/core-patch-pins.json) against this checkout")
    parser.add_argument("--plugins-root", metavar="DIR",
                        help="With --verify-pins: also check each plugin's expect_ast_sha against its lock")


def _rows() -> list[dict[str, Any]]:
    from hermes_cli import core_patches, core_patches_report as report
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    failures = report._failures()
    return [report.row_of(r, failures) for r in core_patches.records()]


def _print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No core patches registered.")
        return
    for row in rows:
        flag = " critical" if row["critical"] else ""
        detail = f"  {row['detail']}" if row["detail"] else ""
        if row["drifted"]:
            flag += " DRIFTED"
        print(f"{row['status']:<22} {row['plugin']}:{row['name']}  {row['kind']}{flag} order={row['order']}  "
              f"{row['target']}{detail}")
    print(FOOTER)


def _strict_code(rows: list[dict[str, Any]], strict: bool) -> int:
    bad = ("skipped-drift", "skipped-error", "disabled-after-errors")
    return 1 if strict and any(row["status"] in bad or row.get("drifted") for row in rows) else 0


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
            text = lock_diff(entry, None, Path(record.spec.plugin_dir))
        except CorePatchTargetError as exc:
            print(f"{record.spec.key}: {exc}")
            changed += 1
            continue
        if text:
            changed += 1
            print(f"{record.spec.key}: {record.spec.target} changed since the recorded upstream snapshot:")
            print(text, end="" if text.endswith("\n") else "\n")
        else:
            print(f"{record.spec.key}: no change since the recorded upstream snapshot")
    return 1 if changed else 0


def cmd_patches(args: Any) -> None:
    """Dispatch ``hermes plugins patches``; exits non-zero via SystemExit on failure or drift."""
    from hermes_cli.core_patches_ast import CorePatchTargetError, core_patch_sha, migrate_lock, write_lock_entry

    code = 0
    try:
        if getattr(args, "sha", None):
            print(core_patch_sha(args.sha))
        elif getattr(args, "record", None):
            code = _record(args.record, getattr(args, "reason", None))
        elif getattr(args, "upstream_diff", None):
            from hermes_cli import core_patches_prov as prov

            print(prov.upstream_diff(prov.find_record(args.upstream_diff)), end="")
        elif getattr(args, "agent_brief", None):
            from hermes_cli import core_patches_prov as prov

            print(prov.agent_brief(prov.find_record(args.agent_brief)))
        elif getattr(args, "report", False):
            from hermes_cli import core_patches, core_patches_prov as prov
            from hermes_cli.plugins import discover_plugins

            discover_plugins()
            print(prov.report_markdown(core_patches.records()))
            code = _strict_code(_rows(), getattr(args, "strict", False))
        elif getattr(args, "migrate_lock", None):
            print(f"migrated {migrate_lock(Path(args.migrate_lock))} entr(y/ies)")
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
    except (CorePatchTargetError, ValueError, OSError) as exc:  # CorePatchFileError is a CorePatchTargetError
        print(f"hermes plugins patches: {exc}", file=sys.stderr)
        code = 2
    if code:
        raise SystemExit(code)


def _record(name: str, reason: Any) -> int:
    from hermes_cli import core_patches_prov as prov

    if not reason:
        print("--record needs --reason \"one line: the bug or purpose\"", file=sys.stderr)
        return 2
    done = prov.record(prov.find_record(name), str(reason))
    ref = done["upstream_ref"]
    print(f"{done['name']}: recorded {done['ast_sha']} ({done['target_file']}) against upstream {ref['commit']} "
          f"(release {ref['release']}); lock and snapshot written under {done['plugin_dir']}")
    if done["fork_dirty"]:
        print(f"warning: {done['target_file']} has uncommitted changes; commit them, then record again",
              file=sys.stderr)
    if done["stale_expectation"]:
        print(f"next: set expect_ast_sha to {done['ast_sha']} in the plugin ({done['patch_fn'] or 'register_core_patch call'})")
    return 0


def _check_core_patches(should_fix: bool) -> Any:
    """The ``hermes doctor`` row (best-effort: a broken registry must never break doctor)."""
    from hermes_cli.doctor_report import doctor_check

    return doctor_check("")(_doctor_rows)(should_fix)


def _doctor_rows(should_fix: bool, f: Any) -> None:
    from hermes_cli import core_patches, core_patches_prov, core_patches_report as report
    from hermes_cli.doctor_report import _section, check_ok, check_warn
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    current = core_patches.records()
    missing = core_patches._missing_declared()
    if not current and not missing:
        return
    _section("Core Patches")
    counts = ", ".join(f"{status}={n}" for status, n in report._counts(report.row_of(r) for r in current).items())
    drifted = sum(1 for r in current if r.drifted)
    counts += f", drifted={drifted}" if drifted else ""  # spec 09: same rule as the registry summary line
    problems = core_patches_prov.lock_problems(current)
    summary = f"{len(current)} patch(es): {counts or 'none'}" + (f"; {len(problems)} provenance problem(s)" if problems else "")
    note = "(see: hermes plugins patches; hermes plugins patches --agent-brief NAME)"
    (check_ok if all(r.status == core_patches.ACTIVE and not r.drifted for r in current) and not problems and not missing
     else check_warn)("core patches", f"{summary} {note}")
    for record in current:
        label = f"{record.spec.key} -> {record.spec.target}"
        if record.status == core_patches.ACTIVE and not record.drifted:
            check_ok(label, "(active)")
            continue
        check_warn(label, f"({record.status}: {record.detail})")
        if record.drifted:
            f.manual_issues.append(
                f"{'CRITICAL core patch' if record.critical else 'Core patch'} {record.spec.key} on {record.spec.target} "
                f"drifted: {record.detail}. Hermes runs the plugin's patch anyway. Run `hermes plugins patches "
                f"--agent-brief {record.spec.name}`.")
            continue
        loud = "CRITICAL core patch" if record.critical else "Core patch"
        f.manual_issues.append(
            f"{loud} {record.spec.key} on {record.spec.target} is {record.status}: {record.detail}. "
            f"Hermes runs the upstream behaviour for it. Run `hermes plugins patches --agent-brief {record.spec.name}`.")
    for line in missing:
        check_warn("critical core patch", f"({line})")
        f.manual_issues.append(f"CRITICAL core patch not registered: {line}. Hermes starts without it.")
    for line in problems:
        check_warn("provenance", line)
