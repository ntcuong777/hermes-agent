"""Core-patch drift fingerprints: a stable hash of one function's AST, and the lock/pin files.

Fork-owned (never upstream). STDLIB ONLY at module level: the nix `hermes-plugins` check runs this
file as a script (`python3 -I .../hermes_cli/core_patches_ast.py --verify-locks ...`) with a plain
interpreter that has no Hermes dependencies.

The fingerprint of `pkg.mod:func` or `pkg.mod:Class.method` is sha256 over a canonical dump of the
`def` node found in the module's source file (for an imported module: the file `sys.modules[mod]`
was loaded from, so the hash describes the code this process runs, not another checkout): node class names and non-empty fields, no line or
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


class CorePatchFileError(CorePatchTargetError):
    """A lock or pins file is missing, unreadable or malformed."""


@dataclass(frozen=True)
class TargetSource:
    target: str
    path: Path
    source: str  # the def's text, decorators included
    sha: str
    name: str = ""       # the def's name
    first_line: int = 0  # first line of the def, decorators included (what co_firstlineno reports)


def split_target(target: str) -> tuple[str, tuple[str, ...]]:
    """`"pkg.mod:Class.meth"` -> `("pkg.mod", ("Class", "meth"))`."""
    if not isinstance(target, str) or not TARGET_RE.match(target):
        raise CorePatchTargetError(f"target {target!r} must look like 'pkg.mod:function' or 'pkg.mod:Class.method'")
    module, qual = target.split(":", 1)
    return module, tuple(qual.split("."))


def _loaded_file(module: str) -> Optional[Path]:
    """The .py file an already imported `module` was loaded from (None when not imported)."""
    loaded = sys.modules.get(module)
    origin = getattr(loaded, "__file__", None) if loaded is not None else None
    return Path(origin) if isinstance(origin, str) and origin.endswith(".py") and Path(origin).is_file() else None


def module_file(module: str, source_root: Optional[Path] = None) -> Path:
    """The .py file of `module`: with no `source_root`, the file an imported module was loaded from
    (`sys.modules[module].__file__`); else by path under `source_root` (default: the checkout holding
    hermes_cli/), else importlib's spec origin."""
    if source_root is None and (loaded := _loaded_file(module)) is not None:
        return loaded
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
    return TargetSource(target=target, path=path, source="".join(lines), sha=node_sha(node),
                        name=qual[-1], first_line=start)


def runtime_mismatch(source: TargetSource, func: Any) -> Optional[str]:
    """Why the live function is not the `def` that `source` hashed (None when it is): the hash only
    proves what the top-level def text says, so a later `f = other`, a def inside if/try, or a file
    rewritten since import must not pass. Compares the code object's name and first line (decorators
    included) after following `__wrapped__`."""
    inner = func
    for _ in range(32):  # bounded: a __wrapped__ cycle must not hang
        nxt = getattr(inner, "__wrapped__", None)
        if nxt is None or nxt is inner:
            break
        inner = nxt
    code = getattr(inner, "__code__", None)
    if code is None:
        return f"{source.target}: the runtime object is not a Python function; its code cannot be verified"
    if (code.co_name, code.co_firstlineno) != (source.name, source.first_line):
        return (f"{source.target}: the runtime function ({code.co_name} at line {code.co_firstlineno}) is not the "
                f"def at line {source.first_line} of {source.path}")
    return None


def core_patch_sha(target: str, source_root: Optional[Path] = None) -> str:
    """The drift fingerprint authors put in `expect_ast_sha` (`hermes plugins patches --sha`)."""
    return target_source(target, source_root).sha


# ── lock files (<plugin>/core_patches.lock.json) ─────────────────────────────────────────────


def _read_json(path: Path) -> Any:
    """Parse a lock or pins file; an unreadable or malformed one is a CorePatchFileError (exit 2)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise CorePatchFileError(f"{path}: cannot read it ({type(exc).__name__}: {exc})") from None


def read_lock(plugin_dir: Path) -> dict:
    path = Path(plugin_dir) / LOCK_FILE
    if not path.is_file():
        return {}
    data = _read_json(path)
    patches = data.get("patches") if isinstance(data, dict) else None
    if not isinstance(patches, dict):
        raise CorePatchFileError(f"{path}: no 'patches' object")
    return patches


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


def _entry_problem(label: str, entry: Any, source_root: Optional[Path], word: str) -> Optional[str]:
    try:
        current = core_patch_sha(str(entry["target"]), source_root)
    except (CorePatchTargetError, KeyError, TypeError, OSError, UnicodeDecodeError) as exc:
        return f"ERROR {label}: {exc!r}" if not isinstance(exc, CorePatchTargetError) else f"ERROR {label}: {exc}"
    if current != entry.get("ast_sha"):
        return f"DRIFT {label} {entry['target']} {word}={entry.get('ast_sha')} current={current}"
    return None


def _locks(plugins_root: Path) -> list[tuple[Path, Any]]:
    """(plugin dir, its lock patches or the CorePatchFileError) for every lock under `plugins_root`."""
    def load(lock: Path) -> Any:
        try:
            return read_lock(lock.parent)
        except CorePatchFileError as exc:
            return exc
    return [(lock.parent, load(lock)) for lock in sorted(Path(plugins_root).glob(f"*/{LOCK_FILE}"))]


def verify_locks(plugins_root: Path, source_root: Optional[Path] = None) -> list[str]:
    """One line per drifted or broken lock entry under `plugins_root/*/core_patches.lock.json`."""
    return [
        line
        for plugin, patches in _locks(plugins_root)
        for line in ([f"ERROR {plugin.name}: {patches}"] if isinstance(patches, Exception) else [
            _entry_problem(f"{plugin.name}/{name}", entry, source_root, "locked")
            for name, entry in sorted(patches.items())])
        if line
    ]


# ── the plugin's expect_ast_sha literals against its lock ───────────────────────────────────


def _str_constants(tree: ast.Module) -> dict[str, str]:
    return {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    }


def _registrations(path: Path) -> list[tuple[Optional[str], Optional[str]]]:
    """(name, expect_ast_sha) of each ``register_core_patch(...)`` call in one file; None = not static."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError, ValueError):
        return []
    consts = _str_constants(tree)

    def value(node: Optional[ast.AST]) -> Optional[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return consts.get(node.id) if isinstance(node, ast.Name) else None

    def keyword(call: ast.Call, name: str) -> Optional[ast.AST]:
        return next((k.value for k in call.keywords if k.arg == name), None)

    return [
        (value(call.args[0] if call.args else keyword(call, "name")), value(keyword(call, "expect_ast_sha")))
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "register_core_patch"
    ]


def _plugin_python(plugin_dir: Path) -> list[Path]:
    skip = {"tests", "test", ".git", "__pycache__", ".venv", "venv", "node_modules"}
    return [p for p in sorted(Path(plugin_dir).rglob("*.py"))
            if not skip.intersection(p.relative_to(plugin_dir).parts[:-1]) and not p.name.startswith("test_")]


def verify_expectations(plugins_root: Path) -> list[str]:
    """Each plugin's ``expect_ast_sha`` (a literal or a module-level string constant) equals its lock
    entry: a refreshed lock with a stale literal would otherwise pass CI and drift at runtime."""
    problems: list[str] = []
    for plugin, patches in _locks(plugins_root):
        if isinstance(patches, Exception):
            continue  # verify_locks reports it
        for path in _plugin_python(plugin):
            for name, expect in _registrations(path):
                rel = path.relative_to(plugin)
                if name is None or expect is None:
                    problems.append(f"ERROR {plugin.name}/{rel}: a register_core_patch call whose name or "
                                    "expect_ast_sha is not a literal or module constant (cannot verify it)")
                elif name not in patches:
                    problems.append(f"ERROR {plugin.name}/{name}: registered in {rel} but not in {LOCK_FILE}")
                elif patches[name].get("ast_sha") != expect:
                    problems.append(f"MISMATCH {plugin.name}/{name}: expect_ast_sha={expect} in {rel} but "
                                    f"{LOCK_FILE} ast_sha={patches[name].get('ast_sha')}")
    return problems


# ── pins (.github/core-patch-pins.json in the fork: what fork-check verifies) ───────────────


def write_pins(plugins_root: Path, pins_file: Path) -> int:
    broken = [f"{plugin.name}: {patches}" for plugin, patches in _locks(plugins_root) if isinstance(patches, Exception)]
    if broken:
        raise CorePatchFileError("; ".join(broken))
    pins = {
        f"{plugin.name}/{name}": {"target": entry["target"], "ast_sha": entry["ast_sha"]}
        for plugin, patches in _locks(plugins_root)
        for name, entry in sorted(patches.items())
    }
    Path(pins_file).write_text(json.dumps({"version": LOCK_VERSION, "pins": pins}, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    return len(pins)


def read_pins(pins_file: Path) -> dict:
    data = _read_json(Path(pins_file))
    pins = data.get("pins") if isinstance(data, dict) else None
    if not isinstance(pins, dict):
        raise CorePatchFileError(f"{pins_file}: no 'pins' object")
    return pins


def verify_pins(pins_file: Path, source_root: Optional[Path] = None) -> list[str]:
    return [line for label, pin in sorted(read_pins(pins_file).items())
            if (line := _entry_problem(label, pin, source_root, "pinned"))]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="core_patches_ast")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sha", metavar="TARGET")
    group.add_argument("--verify-locks", metavar="PLUGINS_ROOT",
                       help="Verify every lock under PLUGINS_ROOT and each plugin's expect_ast_sha literals")
    group.add_argument("--write-pins", nargs=2, metavar=("PLUGINS_ROOT", "PINS_FILE"))
    group.add_argument("--verify-pins", metavar="PINS_FILE")
    parser.add_argument("--plugins-root", type=Path, default=None,
                        help="With --verify-pins: also check each plugin's expect_ast_sha against its lock")
    parser.add_argument("--source-root", type=Path, default=None)
    args = parser.parse_args(argv)
    return run(args)


def run(args: Any) -> int:
    """Shared by this script and ``hermes plugins patches --verify-pins``. 0 ok, 1 drift, 2 unusable input."""
    try:
        if getattr(args, "sha", None):
            print(core_patch_sha(args.sha, args.source_root))
            return 0
        if getattr(args, "write_pins", None):
            print(f"wrote {write_pins(Path(args.write_pins[0]), Path(args.write_pins[1]))} pin(s)")
            return 0
        if args.verify_locks:
            root = Path(args.verify_locks)
            checked = sum(len(p) for _, p in _locks(root) if not isinstance(p, Exception))
            problems = verify_locks(root, args.source_root) + verify_expectations(root)
        else:
            checked = len(read_pins(Path(args.verify_pins)))
            problems = verify_pins(Path(args.verify_pins), args.source_root) + (
                verify_expectations(args.plugins_root) if args.plugins_root else [])
    except CorePatchTargetError as exc:
        print(f"core_patches_ast: {exc}", file=sys.stderr)
        return 2
    for line in problems:
        print(line, file=sys.stderr)
    if problems:
        print(f"core patches: {len(problems)} problem(s)")
        return 1
    print("core patches: no pins recorded (nothing verified)" if args.verify_pins and not checked
          else "core patches: no locks found (nothing verified)" if not checked
          else f"core patches: no drift ({checked} entr{'y' if checked == 1 else 'ies'} verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
