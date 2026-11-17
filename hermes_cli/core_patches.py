"""Core-patch registry: plugins wrap or replace one upstream function, with a drift guard.

Fork-owned (never upstream). A plugin calls ``ctx.register_core_patch(...)`` from ``register(ctx)``;
the patch is staged, checked (declared in plugin.yaml, granted by
``plugins.entries.<id>.allow_core_patch: true``, target resolvable, AST fingerprint unchanged) and
installed when the plugin's load is published. ``apply_core_patches(surface)`` is the eager entry
each process calls before it builds agents: it runs plugin discovery (which installs the patches)
and refuses startup when a ``critical`` patch could not be installed.

Statuses: pending (staged, load not yet published), active, skipped-drift, skipped-no-grant,
skipped-error.
"""

from __future__ import annotations

import inspect
import importlib
import itertools
import logging
import re
import sys
import threading
import types
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

from hermes_cli.core_patches_ast import SHA_RE, TARGET_RE, CorePatchTargetError, core_patch_sha, split_target

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
KINDS = ("wrap", "replace")
PENDING, ACTIVE = "pending", "active"
SKIPPED_DRIFT, SKIPPED_NO_GRANT, SKIPPED_ERROR = "skipped-drift", "skipped-no-grant", "skipped-error"
REFUSE_STATUSES = frozenset({SKIPPED_DRIFT, SKIPPED_ERROR})
REFUSAL_EXIT_CODE = 78  # EX_CONFIG
MARKER = "__hermes_core_patch__"
_MISSING = object()


class CorePatchRefusal(SystemExit):
    """A critical core patch is not installed: the process must not start."""

    def __init__(self, message: str) -> None:
        super().__init__(REFUSAL_EXIT_CODE)
        self.message = message


@dataclass(frozen=True)
class CorePatchSpec:
    plugin_id: str
    name: str
    target: str
    kind: str
    fn: Callable[..., Any]
    expect_ast_sha: str
    order: int
    critical: bool
    plugin_dir: Optional[str]
    seq: int

    @property
    def key(self) -> str:
        return f"{self.plugin_id}:{self.name}"


@dataclass(frozen=True)
class PatchRecord:
    spec: CorePatchSpec
    status: str
    detail: str = ""
    owners: frozenset = frozenset()


@dataclass(frozen=True)
class _TargetState:
    owner: Any                 # the module or class holding the attribute
    attr: str
    is_function: bool          # module-level function (aliases are rebound) vs class attribute
    descriptor: Optional[type]  # staticmethod / classmethod wrapper of the original, else None
    original_raw: Any          # what vars(owner)[attr] held before any patch
    original_func: Callable[..., Any]
    installed_raw: Any
    installed_func: Callable[..., Any]
    aliases: frozenset = field(default_factory=frozenset)


_LOCK = threading.RLock()
_SEQ = itertools.count()
_RECORDS: dict[str, PatchRecord] = {}
_TARGETS: dict[str, _TargetState] = {}
_LOGGED: set[tuple[str, str, str]] = set()


# ── spec validation ──────────────────────────────────────────────────────────────────────────


def make_spec(*, plugin_id: str, name: str, target: str, kind: str, fn: Callable[..., Any],
              expect_ast_sha: str, order: int = 0, critical: bool = False,
              plugin_dir: Optional[str] = None) -> CorePatchSpec:
    """Validate the arguments of ``ctx.register_core_patch``; a programming error raises."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f"core patch name {name!r} must match {NAME_RE.pattern}")
    if not isinstance(target, str) or not TARGET_RE.match(target):
        raise ValueError(f"core patch target {target!r} must look like 'pkg.mod:function' or 'pkg.mod:Class.method'")
    if kind not in KINDS:
        raise ValueError(f"core patch kind {kind!r} must be one of {KINDS}")
    if not callable(fn):
        raise TypeError("core patch fn must be callable")
    if not isinstance(expect_ast_sha, str) or not SHA_RE.match(expect_ast_sha):
        raise ValueError("expect_ast_sha must be 64 lowercase hex characters (hermes plugins patches --sha TARGET)")
    if isinstance(order, bool) or not isinstance(order, int):
        raise TypeError("order must be an int")
    if not isinstance(critical, bool):
        raise TypeError("critical must be a bool")
    return CorePatchSpec(plugin_id=plugin_id, name=name, target=target, kind=kind, fn=fn,
                         expect_ast_sha=expect_ast_sha, order=order, critical=critical,
                         plugin_dir=str(plugin_dir) if plugin_dir else None, seq=next(_SEQ))


# ── grant and declaration ────────────────────────────────────────────────────────────────────


def core_patch_granted(plugin_id: str, home_path: Optional[Path] = None) -> bool:
    """``plugins.entries.<plugin_id>.allow_core_patch`` is exactly ``true`` in that home's config.
    Fail closed: unreadable config never grants."""
    try:
        from hermes_cli.config import load_config
        from hermes_cli.plugins_loader import _plugin_home_scope

        if home_path is None:
            cfg = load_config() or {}
        else:
            with _plugin_home_scope(Path(home_path)):
                cfg = load_config() or {}
        entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(plugin_id) or {}
    except Exception:  # health: allow BLE001 -- fail closed
        return False
    return isinstance(entry, dict) and entry.get("allow_core_patch") is True


def declared_patch_names(plugin_dir: Optional[str]) -> frozenset:
    """Names listed under ``core_patches:`` in the plugin's plugin.yaml (empty when unreadable)."""
    if not plugin_dir:
        return frozenset()
    try:
        from utils import fast_safe_load  # the reader parse_manifest_file uses (hermes_yaml)

        data = fast_safe_load((Path(plugin_dir) / "plugin.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # health: allow BLE001 -- an unreadable manifest declares nothing
        return frozenset()
    raw = data.get("core_patches") if isinstance(data, dict) else None
    return frozenset(item for item in raw if isinstance(item, str)) if isinstance(raw, list) else frozenset()


# ── target resolution and installation ───────────────────────────────────────────────────────


def _resolve(target: str) -> _TargetState:
    module_name, qual = split_target(target)
    module = importlib.import_module(module_name)
    owner: Any = module
    if len(qual) == 2:
        owner = vars(module).get(qual[0], _MISSING)
        if not isinstance(owner, type):
            raise CorePatchTargetError(f"{module_name}.{qual[0]} is not a class")
    attr = qual[-1]
    raw = vars(owner).get(attr, _MISSING)
    if raw is _MISSING:
        raise CorePatchTargetError(f"{target}: {attr!r} is not defined on {owner.__name__} itself")
    if isinstance(raw, property):
        raise CorePatchTargetError(f"{target} is a property; properties cannot be patched")
    descriptor = type(raw) if isinstance(raw, (staticmethod, classmethod)) else None
    func = raw.__func__ if descriptor else raw
    if not callable(func):
        raise CorePatchTargetError(f"{target} is not callable")
    return _TargetState(owner=owner, attr=attr, is_function=len(qual) == 1, descriptor=descriptor,
                        original_raw=raw, original_func=func, installed_raw=raw, installed_func=func)


def _target_state(target: str) -> _TargetState:
    state = _TARGETS.get(target)
    if state is None:
        state = _resolve(target)
        _TARGETS[target] = state
    return state


def _skip_alias_module(module: types.ModuleType, skip: frozenset) -> bool:
    name = getattr(module, "__name__", "") or ""
    return name in skip or name.startswith("hermes_plugins.") or name == __name__


def _install(target: str, state: _TargetState, new_func: Callable[..., Any], skip_modules: frozenset) -> bool:
    """Put ``new_func`` on the owner and rebind ``from x import f`` aliases by identity. False when
    something outside the registry replaced the attribute since we last installed it."""
    if vars(state.owner).get(state.attr, _MISSING) is not state.installed_raw:
        return False
    restoring = new_func is state.original_func
    new_raw = state.original_raw if restoring else (state.descriptor(new_func) if state.descriptor else new_func)
    setattr(state.owner, state.attr, new_raw)
    aliases = set(state.aliases)
    if state.is_function:
        for module in list(sys.modules.values()):
            if not isinstance(module, types.ModuleType) or module is state.owner or _skip_alias_module(module, skip_modules):
                continue
            try:
                items = list(vars(module).items())
            except Exception:  # health: allow BLE001 -- an exotic module object is simply skipped
                continue
            for alias, value in items:
                if value is state.installed_func or value is state.original_func:
                    try:
                        setattr(module, alias, new_func)
                    except Exception:  # health: allow BLE001 -- a read-only module attribute stays
                        continue
                    aliases.add((module.__name__, alias))
    _TARGETS[target] = replace(state, installed_raw=new_raw, installed_func=new_func,
                               aliases=frozenset() if restoring else frozenset(aliases))
    return True


def _mark(func: Callable[..., Any], spec: CorePatchSpec) -> None:
    try:
        setattr(func, MARKER, spec.key)
    except (AttributeError, TypeError):
        pass


def _rebuild(target: str, adding: Optional[str] = None) -> bool:
    """Restore the target's original, then apply every active patch (plus ``adding``) in order:
    the one ``replace`` first, then wraps by (order, registration sequence). Lower order = closer
    to the original (inner); the highest order runs first."""
    try:
        state = _target_state(target)
    except Exception as exc:  # health: allow BLE001 -- recorded as the patch's status
        if adding:
            _set_status(adding, SKIPPED_ERROR, f"target unresolvable: {exc}")
        return False
    members = sorted(
        (r for r in _RECORDS.values() if r.spec.target == target and (r.status == ACTIVE or r.spec.key == adding)),
        key=lambda r: (r.spec.kind != "replace", r.spec.order, r.spec.seq))
    is_async = inspect.iscoroutinefunction(state.original_func)
    current = state.original_func
    outcome: dict[str, tuple[str, str]] = {}
    for record in members:
        spec = record.spec
        try:
            candidate = spec.fn if spec.kind == "replace" else spec.fn(current)
        except Exception as exc:  # health: allow BLE001 -- a broken factory skips its own patch only
            outcome[spec.key] = (SKIPPED_ERROR, f"wrap factory raised {exc!r}")
            continue
        if not callable(candidate):
            outcome[spec.key] = (SKIPPED_ERROR, "wrap factory returned a non-callable")
            continue
        if inspect.iscoroutinefunction(candidate) != is_async:
            outcome[spec.key] = (SKIPPED_ERROR, "async-ness differs from the target (async target needs an async function)")
            continue
        _mark(candidate, spec)
        current = candidate
        outcome[spec.key] = (ACTIVE, "")
    skip = frozenset(getattr(r.spec.fn, "__module__", "") or "" for r in members)
    if not _install(target, state, current, skip):
        for key in outcome:
            _set_status(key, SKIPPED_ERROR, f"{target} was replaced outside the registry; restart to recover")
        return False
    for key, (status, detail) in outcome.items():
        _set_status(key, status, detail)
    return True


def _set_status(key: str, status: str, detail: str) -> None:
    record = _RECORDS.get(key)
    if record is None:
        return
    _RECORDS[key] = replace(record, status=status, detail=detail)
    if status != ACTIVE:
        _log_once(key, status, detail)


def _log_once(key: str, status: str, detail: str) -> None:
    marker = (key, status, detail)
    if marker in _LOGGED:
        return
    _LOGGED.add(marker)
    logger.warning("core patch %s %s: %s", key, status, detail)


# ── staging lifecycle (called by the ctx method and the plugin ledger) ───────────────────────


def _initial_status(spec: CorePatchSpec, *, granted: bool, declared: bool) -> tuple[str, str]:
    if not declared:
        return SKIPPED_ERROR, f"{spec.name!r} is not listed under core_patches: in plugin.yaml"
    if not granted:
        return SKIPPED_NO_GRANT, f"set plugins.entries.{spec.plugin_id}.allow_core_patch: true to allow it"
    try:
        _target_state(spec.target)
        current = core_patch_sha(spec.target)
    except Exception as exc:  # health: allow BLE001 -- recorded as the patch's status
        return SKIPPED_ERROR, f"target unresolvable: {exc}"
    if current != spec.expect_ast_sha:
        return SKIPPED_DRIFT, (f"{spec.target} changed (expected {spec.expect_ast_sha[:12]}, now {current[:12]}); "
                               f"see hermes plugins patches --diff {spec.name}")
    if spec.kind == "replace":
        other = next((r for r in _RECORDS.values() if r.spec.target == spec.target and r.spec.kind == "replace"
                      and r.status in (PENDING, ACTIVE) and r.spec.key != spec.key), None)
        if other is not None:
            return SKIPPED_ERROR, f"{spec.target} is already replaced by {other.spec.key}"
    return PENDING, ""


def stage(spec: CorePatchSpec, owner: str, *, granted: bool, declared: bool) -> str:
    """Record a registration; returns its key. A second manager registering the same patch only
    adds itself as an owner (the patch is process-global and installed once)."""
    with _LOCK:
        existing = _RECORDS.get(spec.key)
        if existing is not None:
            if (existing.spec.target, existing.spec.kind) != (spec.target, spec.kind):
                raise ValueError(f"core patch {spec.key} is already registered for {existing.spec.target}")
            _RECORDS[spec.key] = replace(existing, owners=existing.owners | {owner})
            return spec.key
        status, detail = _initial_status(spec, granted=granted, declared=declared)
        _RECORDS[spec.key] = PatchRecord(spec=spec, status=status, detail=detail, owners=frozenset({owner}))
        if status != PENDING:
            _log_once(spec.key, status, detail)
        return spec.key


def activate(key: str, owner: str) -> None:
    """Install a pending patch (the plugin's load succeeded). No-op for any other status."""
    with _LOCK:
        record = _RECORDS.get(key)
        if record is not None and record.status == PENDING and owner in record.owners:
            _rebuild(record.spec.target, adding=key)


def release(key: str, owner: str) -> None:
    """Undo (plugin unload or failed load): drop ``owner``; the last owner removes the patch."""
    with _LOCK:
        record = _RECORDS.get(key)
        if record is None or owner not in record.owners:
            return
        owners = record.owners - {owner}
        if owners:
            _RECORDS[key] = replace(record, owners=owners)
            return
        del _RECORDS[key]
        if record.status == ACTIVE and not _rebuild(record.spec.target):
            logger.warning("core patch %s: could not undo it on %s (replaced outside the registry); restart to remove",
                           key, record.spec.target)


def records() -> tuple[PatchRecord, ...]:
    with _LOCK:
        return tuple(sorted(_RECORDS.values(), key=lambda r: (r.spec.target, r.spec.order, r.spec.seq)))


# ── eager entry point ───────────────────────────────────────────────────────────────────────

_SUMMARY_LOGGED: set[tuple[str, ...]] = set()


def apply_core_patches(surface: str = "") -> tuple[PatchRecord, ...]:
    """Discover plugins now (installing their patches) and refuse startup on a broken critical one.
    Idempotent: safe to call from every entry point and more than once."""
    try:
        from hermes_cli.plugins import discover_plugins

        discover_plugins()
    except Exception:  # health: allow BLE001 -- discovery failure is reported where it happens
        logger.warning("core patches: plugin discovery failed (%s)", surface or "?", exc_info=True)
    current = records()
    summary = tuple(f"{r.spec.key}={r.status}" for r in current)
    if current and summary not in _SUMMARY_LOGGED:
        _SUMMARY_LOGGED.add(summary)
        logger.info("core patches (%s): %s", surface or "?", ", ".join(summary))
    refused = [r for r in current if r.spec.critical and r.status in REFUSE_STATUSES]
    if refused:
        message = "refusing to start: critical core patch " + "; ".join(
            f"{r.spec.key} on {r.spec.target} is {r.status} ({r.detail})" for r in refused)
        logger.error("core patches: %s", message)
        print(f"hermes: {message}", file=sys.stderr)
        raise CorePatchRefusal(message)
    return current


def _reset_for_tests() -> None:
    """Test-only: restore every patched target and forget all state."""
    with _LOCK:
        for target, state in list(_TARGETS.items()):
            if vars(state.owner).get(state.attr, _MISSING) is state.installed_raw:
                _install(target, state, state.original_func, frozenset())
        _RECORDS.clear()
        _TARGETS.clear()
        _LOGGED.clear()
        _SUMMARY_LOGGED.clear()
