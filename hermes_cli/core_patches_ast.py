"""Core-patch drift fingerprints: a stable hash of one function's AST, and the lock/pin files.

Fork-owned (never upstream). STDLIB ONLY at module level: the nix `hermes-plugins` check runs this
file as a script (`python3 -I .../hermes_cli/core_patches_ast.py --verify-locks ...`) with a plain
interpreter that has no Hermes dependencies.

The fingerprint of `pkg.mod:func` or `pkg.mod:Class.method` is sha256 over a canonical dump of the
`def` node found in the module's source file: node class names and non-empty fields, no line or
column attributes, no `type_comment`, and the function's own docstring removed. Comments, blank
lines, formatting and the docstring therefore never drift it; any change to the code does. Fields
that are None or [] are omitted so a newer Python adding an empty field (3.12 `type_params`) does
not change the hash.
"""

from __future__ import annotations

import argparse
import ast
import copy
import difflib
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

TARGET_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
LOCK_FILE = "core_patches.lock.json"
LOCK_VERSION = 1
DEFAULT_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


class CorePatchTargetError(ValueError):
    """The target string is malformed, its module file is missing, or it has no such def."""


@dataclass(frozen=True)
class TargetSource:
    target: str
    path: Path
    source: str  # the def's text, decorators included
    sha: str


def split_target(target: str) -> tuple[str, tuple[str, ...]]:
    """`"pkg.mod:Class.meth"` -> `("pkg.mod", ("Class", "meth"))`."""
    if not isinstance(target, str) or not TARGET_RE.match(target):
        raise CorePatchTargetError(f"target {target!r} must look like 'pkg.mod:function' or 'pkg.mod:Class.method'")
    module, qual = target.split(":", 1)
    return module, tuple(qual.split("."))


def module_file(module: str, source_root: Optional[Path] = None) -> Path:
    """The .py file of `module`: by path under `source_root` (default: the checkout holding
    hermes_cli/), else importlib's spec origin."""
    root = Path(source_root) if source_root is not None else DEFAULT_SOURCE_ROOT
    rel = Path(*module.split("."))
    for candidate in (root / rel.with_suffix(".py"), root / rel / "__init__.py"):
        if candidate.is_file():
            return candidate
    if source_root is None:
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError) as exc:
            raise CorePatchTargetError(f"module {module!r} not found: {exc}") from None
        origin = getattr(spec, "origin", None)
        if origin and origin.endswith(".py") and Path(origin).is_file():
            return Path(origin)
    raise CorePatchTargetError(f"no source file for module {module!r} under {root}")


def _last_named(body: Sequence[ast.stmt], kinds: tuple, name: str) -> Optional[ast.AST]:
    found = None
    for node in body:
        if isinstance(node, kinds) and getattr(node, "name", None) == name:
            found = node  # the last definition wins, as at runtime
    return found


def find_def(tree: ast.Module, qual: tuple[str, ...]) -> Optional[ast.AST]:
    if len(qual) == 1:
        return _last_named(tree.body, _DEFS, qual[0])
    cls = _last_named(tree.body, (ast.ClassDef,), qual[0])
    return None if cls is None else _last_named(cls.body, _DEFS, qual[1])


def _strip_docstring(node: ast.AST) -> ast.AST:
    clone = copy.deepcopy(node)
    body = getattr(clone, "body", [])
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        clone.body = body[1:]
    return clone


def canonical_dump(node: Any) -> str:
    """Line-free, version-tolerant dump: class name plus every field that is not None/[]."""
    if isinstance(node, ast.AST):
        parts = []
        for field in node._fields:
            if field == "type_comment":
                continue
            value = getattr(node, field, None)
            if value is None or (isinstance(value, list) and not value):
                continue
            parts.append(f"{field}={canonical_dump(value)}")
        return f"{type(node).__name__}({', '.join(parts)})"
    if isinstance(node, list):
        return "[" + ", ".join(canonical_dump(item) for item in node) + "]"
    return repr(node)


def node_sha(node: ast.AST) -> str:
    return hashlib.sha256(canonical_dump(_strip_docstring(node)).encode("utf-8")).hexdigest()


def target_source(target: str, source_root: Optional[Path] = None) -> TargetSource:
    module, qual = split_target(target)
    path = module_file(module, source_root)
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        raise CorePatchTargetError(f"{path} does not parse: {exc}") from None
    node = find_def(tree, qual)
    if node is None:
        raise CorePatchTargetError(f"no top-level def for {'.'.join(qual)!r} in {path}")
    start = min([d.lineno for d in node.decorator_list] + [node.lineno])
    lines = text.splitlines(keepends=True)[start - 1:node.end_lineno]
    return TargetSource(target=target, path=path, source="".join(lines), sha=node_sha(node))


def core_patch_sha(target: str, source_root: Optional[Path] = None) -> str:
    """The drift fingerprint authors put in `expect_ast_sha` (`hermes plugins patches --sha`)."""
    return target_source(target, source_root).sha


# ── lock files (<plugin>/core_patches.lock.json) ─────────────────────────────────────────────


def read_lock(plugin_dir: Path) -> dict:
    path = Path(plugin_dir) / LOCK_FILE
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    patches = data.get("patches") if isinstance(data, dict) else None
    return patches if isinstance(patches, dict) else {}


def write_lock_entry(plugin_dir: Path, name: str, target: str, source_root: Optional[Path] = None) -> str:
    """Record `target`'s current source and fingerprint under `name`; returns the sha."""
    current = target_source(target, source_root)
    path = Path(plugin_dir) / LOCK_FILE
    patches = dict(read_lock(plugin_dir))
    patches[name] = {
        "target": target, "ast_sha": current.sha, "source": current.source,
        "python": "%d.%d.%d" % sys.version_info[:3],
    }
    body = {"version": LOCK_VERSION, "patches": dict(sorted(patches.items()))}
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return current.sha


def lock_diff(entry: Mapping[str, Any], source_root: Optional[Path] = None) -> str:
    """Unified diff from the locked source to the current source ("" when unchanged)."""
    current = target_source(str(entry["target"]), source_root).source
    return "".join(difflib.unified_diff(
        str(entry.get("source", "")).splitlines(keepends=True), current.splitlines(keepends=True),
        fromfile=f"locked {entry['target']}", tofile=f"current {entry['target']}"))


def verify_locks(plugins_root: Path, source_root: Optional[Path] = None) -> list[str]:
    """One line per drifted or broken lock entry under `plugins_root/*/core_patches.lock.json`."""
    problems: list[str] = []
    for lock in sorted(Path(plugins_root).glob(f"*/{LOCK_FILE}")):
        for name, entry in sorted(read_lock(lock.parent).items()):
            label = f"{lock.parent.name}/{name}"
            try:
                current = core_patch_sha(str(entry["target"]), source_root)
            except (CorePatchTargetError, KeyError, OSError) as exc:
                problems.append(f"ERROR {label}: {exc}")
                continue
            if current != entry.get("ast_sha"):
                problems.append(f"DRIFT {label} {entry['target']} locked={entry.get('ast_sha')} current={current}")
    return problems


# ── pins (.github/core-patch-pins.json in the fork: what fork-check verifies) ───────────────


def write_pins(plugins_root: Path, pins_file: Path) -> int:
    pins = {
        f"{lock.parent.name}/{name}": {"target": entry["target"], "ast_sha": entry["ast_sha"]}
        for lock in sorted(Path(plugins_root).glob(f"*/{LOCK_FILE}"))
        for name, entry in sorted(read_lock(lock.parent).items())
    }
    Path(pins_file).write_text(json.dumps({"version": LOCK_VERSION, "pins": pins}, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    return len(pins)


def verify_pins(pins_file: Path, source_root: Optional[Path] = None) -> list[str]:
    data = json.loads(Path(pins_file).read_text(encoding="utf-8"))
    problems: list[str] = []
    for label, pin in sorted((data.get("pins") or {}).items()):
        try:
            current = core_patch_sha(str(pin["target"]), source_root)
        except (CorePatchTargetError, KeyError, OSError) as exc:
            problems.append(f"ERROR {label}: {exc}")
            continue
        if current != pin.get("ast_sha"):
            problems.append(f"DRIFT {label} {pin['target']} pinned={pin.get('ast_sha')} current={current}")
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="core_patches_ast")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sha", metavar="TARGET")
    group.add_argument("--verify-locks", metavar="PLUGINS_ROOT")
    group.add_argument("--write-pins", nargs=2, metavar=("PLUGINS_ROOT", "PINS_FILE"))
    group.add_argument("--verify-pins", metavar="PINS_FILE")
    parser.add_argument("--source-root", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.sha:
            print(core_patch_sha(args.sha, args.source_root))
            return 0
        if args.write_pins:
            print(f"wrote {write_pins(Path(args.write_pins[0]), Path(args.write_pins[1]))} pin(s)")
            return 0
        problems = (verify_locks(Path(args.verify_locks), args.source_root) if args.verify_locks
                    else verify_pins(Path(args.verify_pins), args.source_root))
    except CorePatchTargetError as exc:
        print(f"core_patches_ast: {exc}", file=sys.stderr)
        return 2
    for line in problems:
        print(line, file=sys.stderr)
    print("core patches: no drift" if not problems else f"core patches: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
