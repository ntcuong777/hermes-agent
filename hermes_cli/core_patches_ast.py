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
import contextlib
import copy
import difflib
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

TARGET_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")  # a patch name (also the snapshot file stem)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_TIMEOUT_S = 10.0
LOCK_FILE = "core_patches.lock.json"
LOCK_VERSION = 2          # v2 = provenance record + snapshot file; v1 (inline `source`) is still read;
                         # optional field snapshot_source ("git:<commit[:12]>" or "working-tree")
SNAPSHOT_DIR = "upstream-source"
PIN_KEYS = ("target", "ast_sha", "plugin", "kind", "target_file", "upstream_commit", "upstream_release", "reason")
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


def _source_of(text: str, path: Path, target: str, qual: tuple[str, ...]) -> TargetSource:
    """The def of ``qual`` in the module text ``text`` (the one locating helper of target_source and of the
    snapshot taken at an upstream commit)."""
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


def target_source(target: str, source_root: Optional[Path] = None) -> TargetSource:
    module, qual = split_target(target)
    path = module_file(module, source_root)
    return _source_of(path.read_text(encoding="utf-8"), path, target, qual)


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


def lock_version(plugin_dir: Path) -> int:
    """The ``version`` of the plugin's lock file (1 when missing or unreadable)."""
    try:
        version = _read_json(Path(plugin_dir) / LOCK_FILE).get("version")
        return version if isinstance(version, int) else 1
    except (CorePatchFileError, AttributeError):
        return 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot_rel(name: str) -> str:
    return f"{SNAPSHOT_DIR}/{name}.py.txt"


def _snapshot_file(plugin_dir: Path, rel: Any) -> Optional[Path]:
    """The snapshot file named by a lock entry, only if it stays under ``upstream-source/``."""
    if not isinstance(rel, str) or not rel.startswith(SNAPSHOT_DIR + "/") or ".." in Path(rel).parts:
        return None
    return Path(plugin_dir) / rel


def snapshot_text(plugin_dir: Optional[Path], entry: Mapping[str, Any]) -> Optional[str]:
    """The recorded ORIGINAL upstream source of a patch's target: the v2 snapshot file, or a v1 entry's
    inline ``source``. None when it is missing."""
    path = _snapshot_file(plugin_dir, entry.get("snapshot")) if plugin_dir is not None else None
    if path is not None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None
    inline = entry.get("source")
    return inline if isinstance(inline, str) else None


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _migrated(plugin_dir: Path, patches: Mapping[str, Any]) -> dict:
    """Every entry as a v2 entry: a v1 entry's inline ``source`` moves to its snapshot file (entry gets
    ``migrated_from: 1``; provenance fields it never had stay absent, so the lint reports them). Only keys
    that are patch names (NAME_RE) are migrated; any other key is kept as it is."""
    out: dict = {}
    for name, entry in patches.items():
        if not NAME_RE.match(str(name)):
            out[name] = dict(entry) if isinstance(entry, dict) else entry
            continue
        entry = dict(entry) if isinstance(entry, dict) else {}
        if "source" in entry and "snapshot" not in entry:
            text = str(entry.pop("source"))
            _write_text(Path(plugin_dir) / snapshot_rel(name), text)
            entry.update(snapshot=snapshot_rel(name), snapshot_sha256=_sha256(text), migrated_from=1)
        entry.pop("source", None)
        out[name] = entry
    return out


def _write_lock(plugin_dir: Path, patches: Mapping[str, Any]) -> None:
    body = {"version": LOCK_VERSION, "patches": dict(sorted(patches.items()))}
    _write_text(Path(plugin_dir) / LOCK_FILE, json.dumps(body, indent=2, sort_keys=True) + "\n")


def migrate_lock(plugin_dir: Path) -> int:
    """Rewrite a v1 lock as v2 in place (snapshot files, no inline source). Returns how many entries moved."""
    with _lock_file_held(Path(plugin_dir)):
        if lock_version(plugin_dir) > LOCK_VERSION:
            raise CorePatchFileError("lock file is a newer schema; update the fork first")
        patches = read_lock(plugin_dir)
        moved = sum(1 for e in patches.values() if isinstance(e, dict) and "source" in e and "snapshot" not in e)
        if moved or lock_version(plugin_dir) != LOCK_VERSION:
            _write_lock(plugin_dir, _migrated(plugin_dir, patches))
        return moved


def target_file_rel(current: TargetSource, source_root: Optional[Path] = None) -> str:
    """The target's file relative to the checkout (``tools/terminal_tool.py``)."""
    root = Path(source_root) if source_root is not None else DEFAULT_SOURCE_ROOT
    try:
        return Path(current.path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(current.path).name


@contextlib.contextmanager
def _lock_file_held(plugin_dir: Path) -> Iterator[None]:
    """An exclusive flock on the sibling ``<lock file>.lock`` (a no-op where fcntl is missing)."""
    try:
        import fcntl
    except ImportError:
        yield
        return
    plugin_dir.mkdir(parents=True, exist_ok=True)
    with open(plugin_dir / (LOCK_FILE + ".lock"), "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _committed_text(source_root: Optional[Path], commit: str, rel: str) -> Optional[str]:
    """The file ``rel`` at the upstream ``commit`` (git, never fetching; None when it cannot be read)."""
    root = Path(source_root) if source_root is not None else DEFAULT_SOURCE_ROOT
    if not COMMIT_RE.match(commit or "") or not rel:
        return None
    try:
        run_ = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{rel}"], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_S, check=False, stdin=subprocess.DEVNULL,
                              start_new_session=True,
                              env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
                                   "GIT_ASKPASS": "true"})
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return run_.stdout if run_.returncode == 0 else None


def _upstream_snapshot(current: TargetSource, target: str, source_root: Optional[Path], commit: str,
                       rel: str) -> tuple[str, str]:
    """``(snapshot text, snapshot_source)``: the target's def at the upstream commit when git has it, else the
    working-tree def (``working-tree``). Never raises."""
    text = _committed_text(source_root, commit, rel)
    if text is not None:
        try:
            return _source_of(text, current.path, target, split_target(target)[1]).source, f"git:{commit[:12]}"
        except CorePatchTargetError:
            pass
    return current.source, "working-tree"


def write_lock_entry(plugin_dir: Path, name: str, target: str, source_root: Optional[Path] = None,
                     *, provenance: Optional[Mapping[str, Any]] = None) -> str:
    """Record `target`'s source (a snapshot file) and fingerprint under `name`; returns the sha. The snapshot is
    the def at the recorded upstream commit (``snapshot_source`` = ``git:<commit[:12]>``) when git can read it,
    else the working-tree def (``snapshot_source`` = ``working-tree``). `provenance` (reason, fork_head,
    upstream_ref, ...) is merged into the entry. `authored_at` is kept from an earlier record; `recorded_at` is
    refreshed. Other v1 entries of the lock are migrated. A lock with a newer schema is refused, unchanged."""
    current = target_source(target, source_root)
    plugin_dir = Path(plugin_dir)
    upstream = (provenance or {}).get("upstream_ref")
    commit = str(upstream.get("commit") or "") if isinstance(upstream, dict) else ""
    snapshot, snapshot_source = _upstream_snapshot(current, target, source_root, commit,
                                                   target_file_rel(current, source_root))
    with _lock_file_held(plugin_dir):
        if lock_version(plugin_dir) > LOCK_VERSION:
            raise CorePatchFileError("lock file is a newer schema; update the fork first")
        patches = _migrated(plugin_dir, read_lock(plugin_dir))
        now = _utc_now()
        entry = dict(patches.get(name, {}))
        entry.update(target=target, ast_sha=current.sha, python="%d.%d.%d" % sys.version_info[:3],
                     target_file=target_file_rel(current, source_root), snapshot=snapshot_rel(name),
                     snapshot_sha256=_sha256(snapshot), snapshot_source=snapshot_source, recorded_at=now)
        entry.setdefault("authored_at", now)
        entry.update(dict(provenance or {}))
        _write_text(plugin_dir / snapshot_rel(name), snapshot)
        patches[name] = entry
        _write_lock(plugin_dir, patches)
    return current.sha


def lock_diff(entry: Mapping[str, Any], source_root: Optional[Path] = None, plugin_dir: Optional[Path] = None) -> str:
    """Unified diff from the recorded upstream source to the current source ("" when unchanged)."""
    old = snapshot_text(plugin_dir, entry)
    if old is None:
        raise CorePatchFileError(f"{entry.get('target')}: no recorded source (snapshot file missing); "
                                 "refresh with hermes plugins patches --record NAME --reason ...")
    current = target_source(str(entry["target"]), source_root).source
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), current.splitlines(keepends=True),
        fromfile=f"recorded {entry['target']}", tofile=f"current {entry['target']}"))


# ── provenance lint (CI only: a failure here is a red check, never an agent failure) ────────


def provenance_problems(label: str, entry: Any, plugin_dir: Optional[Path] = None) -> list[str]:
    """What is missing from a lock entry's provenance record (``reason``, ``authored_at``, ``fork_head``,
    ``upstream_ref.commit``, a snapshot whose hash matches). Empty when complete."""
    if not isinstance(entry, dict):
        return [f"PROVENANCE {label}: the entry is not an object"]
    fix = f"fix: hermes plugins patches --record {label.split('/')[-1]} --reason \"...\""
    problems = []
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        problems.append(f"PROVENANCE {label}: no reason ({fix})")
    elif "\n" in reason.strip() or len(reason) > 240:
        problems.append(f"PROVENANCE {label}: reason must be one line of at most 240 characters")
    for key in ("authored_at", "fork_head"):
        if not entry.get(key):
            problems.append(f"PROVENANCE {label}: no {key} ({fix})")
    upstream = entry.get("upstream_ref")
    if not isinstance(upstream, dict) or not upstream.get("commit"):
        problems.append(f"PROVENANCE {label}: no upstream_ref.commit ({fix}; 'unknown' is accepted when git history is unavailable)")
    if not entry.get("snapshot"):
        problems.append(f"PROVENANCE {label}: no snapshot of the upstream source (schema v1 entry; {fix})")
    elif plugin_dir is not None:
        text = snapshot_text(plugin_dir, entry)
        if text is None:
            problems.append(f"PROVENANCE {label}: snapshot file {entry.get('snapshot')} is missing")
        elif entry.get("snapshot_sha256") != _sha256(text):
            problems.append(f"PROVENANCE {label}: snapshot file does not match snapshot_sha256")
    return problems


def pin_problems(label: str, pin: Any) -> list[str]:
    """The same lint for a pins-file entry (it carries ``reason`` and ``upstream_commit``)."""
    if not isinstance(pin, dict):
        return [f"PROVENANCE {label}: the pin is not an object"]
    problems = []
    if not isinstance(pin.get("reason"), str) or not pin["reason"].strip():
        problems.append(f"PROVENANCE {label}: the pin has no reason (record it in the plugin lock, then --write-pins)")
    if not pin.get("upstream_commit"):
        problems.append(f"PROVENANCE {label}: the pin has no upstream_commit (record it in the plugin lock, then --write-pins)")
    return problems


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
    """One line per drifted, broken or provenance-less lock entry under `plugins_root/*/core_patches.lock.json`."""
    problems: list[str] = []
    for plugin, patches in _locks(plugins_root):
        if isinstance(patches, Exception):
            problems.append(f"ERROR {plugin.name}: {patches}")
            continue
        for name, entry in sorted(patches.items()):
            label = f"{plugin.name}/{name}"
            line = _entry_problem(label, entry, source_root, "locked")
            problems += ([line] if line else []) + provenance_problems(label, entry, plugin)
    return problems


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


def pin_of(plugin: str, entry: Mapping[str, Any]) -> dict:
    """The fork-side copy of one lock entry (``PIN_KEYS``): enough for fork CI to detect drift and to say
    what the patch is for and which upstream commit it was written against."""
    upstream = entry.get("upstream_ref") if isinstance(entry.get("upstream_ref"), dict) else {}
    return {"target": entry["target"], "ast_sha": entry["ast_sha"], "plugin": plugin,
            "kind": entry.get("kind", ""), "target_file": entry.get("target_file", ""),
            "upstream_commit": upstream.get("commit", ""), "upstream_release": upstream.get("release", ""),
            "reason": entry.get("reason", "")}


def write_pins(plugins_root: Path, pins_file: Path) -> int:
    broken = [f"{plugin.name}: {patches}" for plugin, patches in _locks(plugins_root) if isinstance(patches, Exception)]
    if broken:
        raise CorePatchFileError("; ".join(broken))
    pins = {
        f"{plugin.name}/{name}": pin_of(plugin.name, entry)
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
    problems: list[str] = []
    for label, pin in sorted(read_pins(pins_file).items()):
        line = _entry_problem(label, pin, source_root, "pinned")
        problems += ([line] if line else []) + pin_problems(label, pin)
    return problems


# ── markdown reports (a PR or issue body; never fail) ───────────────────────────────────────

_HEADER = ("| patch | target | state | recorded | current | upstream commit | reason |\n"
           "|---|---|---|---|---|---|---|\n")


def _git(root: Path, *args: str) -> str:
    try:
        run_ = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, check=False)
        return run_.stdout.strip() if run_.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _cell(text: Any) -> str:
    return str(text or "-").replace("|", "\\|").replace("\n", " ")


def _row_line(label: str, entry: Mapping[str, Any], state: str, current: str, commit: str, reason: str) -> str:
    return (f"| `{label}` | `{entry.get('target', '?')}` | {state} | `{str(entry.get('ast_sha', ''))[:12]}` | "
            f"`{current[:12] or '-'}` | `{commit[:12] or 'unknown'}` | {_cell(reason)} |\n")


def _fenced(text: str, lang: str, max_lines: int = 120) -> str:
    lines = text.splitlines()
    clipped = lines[:max_lines] + ([f"... ({len(lines) - max_lines} more lines)"] if len(lines) > max_lines else [])
    return f"```{lang}\n" + "\n".join(clipped) + "\n```\n"


def _state_of(entry: Any, source_root: Optional[Path]) -> tuple[str, str, str]:
    """(state, current sha, detail): ok, DRIFT or ERROR."""
    try:
        current = core_patch_sha(str(entry["target"]), source_root)
    except (CorePatchTargetError, KeyError, TypeError, OSError, UnicodeDecodeError) as exc:
        return "ERROR", "", str(exc)
    return ("ok" if current == entry.get("ast_sha") else "DRIFT"), current, ""


def report_locks(plugins_root: Path, source_root: Optional[Path] = None) -> str:
    """Markdown: a table of every lock entry and, for each drifted one, the diff recorded -> current."""
    table, details, drifted = [], [], 0
    for plugin, patches in _locks(plugins_root):
        if isinstance(patches, Exception):
            details.append(f"### {plugin.name}\n\nUnreadable lock: `{patches}`\n")
            continue
        for name, entry in sorted(patches.items()):
            label = f"{plugin.name}/{name}"
            state, current, why = _state_of(entry, source_root)
            upstream = entry.get("upstream_ref") if isinstance(entry.get("upstream_ref"), dict) else {}
            table.append(_row_line(label, entry, state, current, str(upstream.get("commit") or ""), entry.get("reason")))
            if state == "ok":
                continue
            drifted += 1
            body = why
            if state == "DRIFT":
                try:
                    body = _fenced(lock_diff(entry, source_root, plugin), "diff")
                except CorePatchTargetError as exc:
                    body = str(exc)
            details.append(f"### {label} ({state})\n\nrecorded upstream: `{upstream.get('commit', 'unknown')}` "
                           f"(release {upstream.get('release', 'unknown')}). Agent brief: "
                           f"`hermes plugins patches --agent-brief {name}`\n\n{body}\n")
    head = f"## Core patches: {drifted} of {len(table)} need attention\n\n" if table else "## Core patches\n\nNo locks found.\n"
    return head + (_HEADER + "".join(table) + "\n" if table else "") + "\n".join(details)


def report_pins(pins_file: Path, source_root: Optional[Path] = None) -> str:
    """Markdown from the fork's pins file: a table and, for each drifted pin, the upstream commits since the
    recorded one that touched the target file (needs the git history; the function-level diff is in the
    plugin lock, see scripts/fork/sync-report.sh)."""
    root = Path(source_root) if source_root is not None else DEFAULT_SOURCE_ROOT
    try:
        pins = read_pins(pins_file)
    except CorePatchFileError as exc:
        return f"## Core patches\n\nCannot read the pins: `{exc}`\n"
    table, details, drifted = [], [], 0
    for label, pin in sorted(pins.items()):
        state, current, why = _state_of(pin, root)
        table.append(_row_line(label, pin, state, current, str(pin.get("upstream_commit") or ""), pin.get("reason")))
        if state == "ok":
            continue
        drifted += 1
        commit, path = str(pin.get("upstream_commit") or ""), str(pin.get("target_file") or "")
        history = (_git(root, "log", "--oneline", "-n", "20", f"{commit}..HEAD", "--", path)
                   if commit not in ("", "unknown") and path else "")
        details.append(f"### {label} ({state})\n\n{why or 'The target changed since the pin.'} Recorded against upstream "
                       f"`{commit or 'unknown'}` (release {pin.get('upstream_release') or 'unknown'}).\n\n"
                       + (f"Commits touching `{path}` since then:\n\n" + _fenced(history, "") if history
                          else "No commit list (the recorded commit is unknown or not in this clone).\n")
                       + f"\nFunction-level diff and agent brief: run `scripts/fork/sync-report.sh --plugins-root "
                         f"<nix-darwin>/hermes/plugins` and `hermes plugins patches --agent-brief {label.split('/')[-1]}`.\n")
    head = (f"## Core patches: {drifted} of {len(table)} need attention\n\n" if table
            else "## Core patches\n\nNo patches are pinned.\n")
    return head + (_HEADER + "".join(table) + "\n" if table else "") + "\n".join(details)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="core_patches_ast")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sha", metavar="TARGET")
    group.add_argument("--verify-locks", metavar="PLUGINS_ROOT",
                       help="Verify every lock under PLUGINS_ROOT and each plugin's expect_ast_sha literals")
    group.add_argument("--write-pins", nargs=2, metavar=("PLUGINS_ROOT", "PINS_FILE"))
    group.add_argument("--verify-pins", metavar="PINS_FILE")
    group.add_argument("--report-locks", metavar="PLUGINS_ROOT",
                       help="Print a markdown report of every lock (drift diffs included); exits 0")
    group.add_argument("--report-pins", metavar="PINS_FILE",
                       help="Print a markdown report of the pins (upstream commits touching drifted targets); exits 0")
    group.add_argument("--migrate-lock", metavar="PLUGIN_DIR", help="Rewrite a schema v1 lock as v2 in place")
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
        if getattr(args, "report_locks", None):
            print(report_locks(Path(args.report_locks), args.source_root))
            return 0
        if getattr(args, "report_pins", None):
            print(report_pins(Path(args.report_pins), args.source_root))
            return 0
        if getattr(args, "migrate_lock", None):
            print(f"migrated {migrate_lock(Path(args.migrate_lock))} entr(y/ies) to schema v{LOCK_VERSION}")
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
