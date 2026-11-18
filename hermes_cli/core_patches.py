"""Core-patch registry: plugins wrap or replace one upstream function, with a drift guard.

Fork-owned (never upstream). A plugin calls ``ctx.register_core_patch(...)`` from ``register(ctx)``;
that only STAGES the patch (no target import, no hashing: register() runs on the load-deadline
worker, where importing a heavy module such as run_agent can re-enter plugin discovery and stall
until the deadline). When the plugin's load is published the patch is checked (declared in
plugin.yaml, granted by ``plugins.entries.<id>.allow_core_patch: true``, target resolvable, AST
fingerprint unchanged, the live function is the hashed def) and installed. A target whose module is
not imported yet stays ``pending`` until ``apply_core_patches(surface)`` imports it (outside the
registry lock). ``apply_core_patches`` is the eager entry each process calls before it builds
agents: it runs plugin discovery, installs the deferred patches and refuses startup when a
``critical`` patch could not be installed. It joins the CLI's background discovery thread at once
(gateway run, cron tick, acp, tui), so those surfaces lose the ~150 ms discovery overlap: accepted,
the patch must be in place before the first agent.

Statuses: pending (staged, or published while its target module is not imported yet), active,
skipped-drift, skipped-no-grant, skipped-error, disabled-after-errors (the safety shell switched a
patch that kept raising at call time off for this process; see core_patches_shell.py).

Fail-safe policy (core_patches_policy.py): nothing here may stop, refuse, hang or break the agent. A
patch that is drifted, missing, ungranted, broken, slow or raising is skipped or switched off and the
core runs its upstream behaviour; it is reported (core_patches_report.py: logs, status.json,
events.jsonl, a home-channel notice, the doctor row). ``critical`` makes that louder (ERROR log,
notice, doctor finding) and refuses startup ONLY when ``core_patches.refuse_on_critical: true``.

Rules:
- A patch is process-global, so one installed patch serves every profile home a multiplexed process
  loads. It is installed only if EVERY home that loaded the plugin grants it (and declares it);
  if the homes disagree it is ``skipped-no-grant`` and the detail names the homes without the grant.
  Each owner joining or leaving re-evaluates the patch, so load order does not matter.
- Lifecycle: the registration is a persistent ledger entry (like dashboard-auth providers). A forced
  re-discovery (``discover_plugins(force=True)``, the gateway's reload-plugins) parks it instead of
  undoing it, so the installed chain stays active while the plugin reloads; the new registration
  is staged and swapped in under the lock when its load is published, and the old one is undone
  only if the plugin does not come back. A targeted unload (disable / uninstall) undoes it at once;
  so does a plain ``PluginManager.unload()`` followed by a forced discovery that does not
  re-register it. A load that fails or times out after staging drops the patch; a ``critical`` one
  leaves a tombstone record (``skipped-error: plugin failed to load``) so the refusal still fires.
- ``critical``: the patch argument, or ``core_patches: [{name: x, critical: true}]`` in plugin.yaml.
  A critical patch that is not active at startup, and a declared critical name with no record at all
  from a plugin that was enabled (its register() raised or timed out before staging it), are
  reported loudly (ERROR log, an event, a home-channel notice, a doctor finding) and the process
  starts without the patch. With ``core_patches.refuse_on_critical: true`` the same conditions
  refuse the process instead (exit 78). After startup (any later reload) they are logged at ERROR
  only. A plugin disabled in config or removed from disk is not required: that is the user's choice.
- Drift: the fingerprint is taken from the file the module was loaded from; the file's mtime and
  size are recorded at first resolution and any later change is ``skipped-drift: source changed
  since import`` (the process still runs the old code); the live function's ``co_name`` and
  ``co_firstlineno`` must match the hashed def (a later ``f = other`` or a def inside if/try is
  drift). Residual gap: an edit made between import and first resolution that keeps the def at the
  same line is not detected (restart after ``hermes update``).
- Each wrap factory is called once per registration; the chain is rewired through per-patch links,
  so adding or undoing another patch never re-invokes a factory.

``active`` proves the patched callable is installed on the owner (module or class attribute) and on
every ``from x import f`` alias that existed at install time; it does NOT prove the patched code is
reached. References captured before install stay on the original: values held in dicts
(``REG = {"plain": plain}``), closures, default arguments, ``functools.partial`` objects, bound
methods taken earlier, and modules that import the function after an undo.
"""

from __future__ import annotations

import contextvars
import inspect
import importlib
import itertools
import logging
import os
import re
import sys
import threading
import types
import weakref
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from hermes_cli import core_patches_policy as policy
from hermes_cli import core_patches_shell as shell
from hermes_cli.core_patches_ast import (
    NAME_RE, SHA_RE, TARGET_RE, CorePatchTargetError, TargetSource, runtime_mismatch, split_target, target_source,
)

logger = logging.getLogger(__name__)

KINDS = ("wrap", "replace")
PENDING, ACTIVE = "pending", "active"
SKIPPED_DRIFT, SKIPPED_NO_GRANT, SKIPPED_ERROR = "skipped-drift", "skipped-no-grant", "skipped-error"
DISABLED = "disabled-after-errors"
REFUSE_STATUSES = frozenset({SKIPPED_DRIFT, SKIPPED_ERROR, PENDING})  # "not installed" for a critical patch
LOUD_STATUSES = REFUSE_STATUSES | {DISABLED}
REFUSAL_EXIT_CODE = 78  # EX_CONFIG
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
    propagates: tuple = ()  # exception types the patch raises on purpose: the safety shell lets them through
    on_drift: Optional[str] = None  # "apply" | "skip" for THIS patch; None = core_patches.on_drift (default apply)

    @property
    def key(self) -> str:
        return f"{self.plugin_id}:{self.name}"


@dataclass(frozen=True)
class _Gen:
    """One registration of a patch by one owner (a plugin manager's home)."""
    owner: str
    spec: CorePatchSpec
    granted: bool
    declared: bool
    published: bool = False


@dataclass(frozen=True)
class PatchRecord:
    spec: CorePatchSpec  # the effective registration: newest published, else newest staged
    status: str
    detail: str = ""
    gens: tuple = ()     # () = a tombstone (a critical patch whose plugin failed to load)
    current_sha: str = ""  # fingerprint of the target as last resolved ("" = never resolved)

    @property
    def drifted(self) -> bool:
        """``active`` although the target changed since ``expect_ast_sha`` (``on_drift: apply``): ``detail`` says how."""
        return self.status == ACTIVE and bool(self.detail)

    @property
    def owners(self) -> frozenset:
        return frozenset(g.owner for g in self.gens)

    @property
    def critical(self) -> bool:
        return self.spec.critical or any(g.spec.critical for g in self.gens)


@dataclass(frozen=True)
class _Probe:
    """What resolving a target found (computed outside the lock)."""
    sha: str = ""
    error: str = ""  # -> skipped-error
    drift: str = ""  # -> skipped-drift


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
    foreign: bool = False      # replaced outside the registry: never touched again


class _Link:
    """One installed patch in a target's chain. A wrap factory is called once, with a trampoline that
    forwards to ``inner``; rebuilding the chain only rewires ``inner``."""

    __slots__ = ("key", "wrapped", "error", "inner", "guard")

    def __init__(self, key: str, wrapped: Any = None, error: str = "") -> None:
        self.key, self.wrapped, self.error, self.inner, self.guard = key, wrapped, error, None, None


_LOCK = threading.RLock()
_SEQ = itertools.count()
_RECORDS: dict[str, PatchRecord] = {}
_TARGETS: dict[str, _TargetState] = {}
_LINKS: dict[int, _Link] = {}             # spec.seq -> link
_FILE_STATS: dict[str, tuple[int, int]] = {}  # source path -> (mtime_ns, size) at first resolution
_SOURCES: dict[tuple, TargetSource] = {}   # (target, path, mtime_ns, size) -> parsed def
_EAGER: set[str] = set()                   # surfaces that called apply_core_patches in this process
_MANAGERS: "weakref.WeakSet[Any]" = weakref.WeakSet()
_SETTINGS = policy.Settings()              # refreshed outside the lock by activate() and apply_core_patches()


# ── spec validation ──────────────────────────────────────────────────────────────────────────


def make_spec(*, plugin_id: str, name: str, target: str, kind: str, fn: Callable[..., Any],
              expect_ast_sha: str, order: int = 0, critical: bool = False,
              plugin_dir: Optional[str] = None, propagates: Iterable[type] = (),
              on_drift: Optional[str] = None) -> CorePatchSpec:
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
    declared = tuple(propagates)
    if not all(isinstance(t, type) and issubclass(t, Exception) for t in declared):
        raise TypeError("propagates must be a tuple of Exception subclasses")
    if on_drift not in (None, "apply", "skip"):
        raise ValueError(f"on_drift must be 'apply', 'skip' or None, not {on_drift!r}")
    return CorePatchSpec(plugin_id=plugin_id, name=name, target=target, kind=kind, fn=fn,
                         expect_ast_sha=expect_ast_sha, order=order, critical=critical,
                         plugin_dir=str(plugin_dir) if plugin_dir else None, seq=next(_SEQ),
                         propagates=declared, on_drift=on_drift)


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


def _declared(plugin_dir: Optional[str]) -> dict[str, bool]:
    """``core_patches:`` of the plugin's plugin.yaml: name -> critical. Items are a name or
    ``{name: x, critical: true}``. Empty when unreadable."""
    if not plugin_dir:
        return {}
    try:
        from utils import fast_safe_load  # the reader parse_manifest_file uses (hermes_yaml)

        data = fast_safe_load((Path(plugin_dir) / "plugin.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # health: allow BLE001 -- an unreadable manifest declares nothing
        return {}
    raw = data.get("core_patches") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return {}
    return {
        **{item: False for item in raw if isinstance(item, str)},
        **{item["name"]: item.get("critical") is True for item in raw
           if isinstance(item, dict) and isinstance(item.get("name"), str)},
    }


def declared_patch_names(plugin_dir: Optional[str]) -> frozenset:
    """Names listed under ``core_patches:`` in the plugin's plugin.yaml (empty when unreadable)."""
    return frozenset(_declared(plugin_dir))


def declared_critical_names(plugin_dir: Optional[str]) -> frozenset:
    """Names declared ``{name: x, critical: true}`` under ``core_patches:``."""
    return frozenset(name for name, critical in _declared(plugin_dir).items() if critical)


# ── target resolution (no import under the lock) ─────────────────────────────────────────────


def _initializing(module: Any) -> bool:
    return bool(getattr(getattr(module, "__spec__", None), "_initializing", False))


def _imported(module_name: str) -> Optional[types.ModuleType]:
    """The module when it is fully imported, else None (a module mid-import is not resolvable yet)."""
    module = sys.modules.get(module_name)
    return module if isinstance(module, types.ModuleType) and not _initializing(module) else None


def _live_raw(module: types.ModuleType, target: str) -> tuple[Any, str, Any]:
    """(owner, attr, raw attribute) of ``target`` in an imported ``module``."""
    _, qual = split_target(target)
    owner: Any = module
    if len(qual) == 2:
        owner = vars(module).get(qual[0], _MISSING)
        if not isinstance(owner, type):
            raise CorePatchTargetError(f"{module.__name__}.{qual[0]} is not a class")
    attr = qual[-1]
    raw = vars(owner).get(attr, _MISSING)
    if raw is _MISSING:
        raise CorePatchTargetError(f"{target}: {attr!r} is not defined on {owner.__name__} itself")
    if isinstance(raw, property):
        raise CorePatchTargetError(f"{target} is a property; properties cannot be patched")
    return owner, attr, raw


def _unwrap_descriptor(raw: Any) -> tuple[Optional[type], Any]:
    descriptor = type(raw) if isinstance(raw, (staticmethod, classmethod)) else None
    return descriptor, (raw.__func__ if descriptor else raw)


def _stat(path: Path) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size


def _probe(target: str, *, may_import: bool) -> Optional[_Probe]:
    """Resolve and fingerprint ``target`` WITHOUT the registry lock. None when its module is not
    imported and ``may_import`` is false (the patch stays pending)."""
    module_name, _ = split_target(target)
    module = _imported(module_name)
    if module is None:
        if not may_import:
            return None
        try:
            module = importlib.import_module(module_name)
        except BaseException as exc:  # health: allow BLE001 -- a target that cannot import is the patch's error
            if isinstance(exc, KeyboardInterrupt):
                raise
            return _Probe(error=f"target unresolvable: cannot import {module_name}: {exc!r}")
    try:
        state = _TARGETS.get(target)
        func = state.original_func if state is not None else _unwrap_descriptor(_live_raw(module, target)[2])[1]
        if not callable(func):
            raise CorePatchTargetError(f"{target} is not callable")
        path = Path(getattr(module, "__file__", "") or "")
        if path.suffix != ".py" or not path.is_file():
            raise CorePatchTargetError(f"{module_name} has no Python source file")
        stat = _stat(path)
        first = _FILE_STATS.setdefault(str(path), stat)
        if stat != first:
            return _Probe(drift=f"source changed since import ({path} was rewritten while this process runs "
                                f"the old code; restart to pick up the new code)")
        cache_key = (target, str(path), *stat)
        source = _SOURCES.get(cache_key) or _SOURCES.setdefault(cache_key, target_source(target))
    except Exception as exc:  # health: allow BLE001 -- recorded as the patch's status
        return _Probe(error=f"target unresolvable: {exc}")
    mismatch = runtime_mismatch(source, func)
    return _Probe(sha=source.sha, drift=mismatch or "")


def _target_state(target: str) -> Optional[_TargetState]:
    """The cached state, or a fresh one when the module is imported (never imports)."""
    state = _TARGETS.get(target)
    if state is not None:
        return state
    module = _imported(split_target(target)[0])
    if module is None:
        return None
    owner, attr, raw = _live_raw(module, target)
    descriptor, func = _unwrap_descriptor(raw)
    state = _TargetState(owner=owner, attr=attr, is_function=len(split_target(target)[1]) == 1,
                         descriptor=descriptor, original_raw=raw, original_func=func,
                         installed_raw=raw, installed_func=func)
    _TARGETS[target] = state
    return state


# ── installation ─────────────────────────────────────────────────────────────────────────────


def _skip_alias_module(module: types.ModuleType, skip: frozenset) -> bool:
    name = getattr(module, "__name__", "") or ""
    return name in skip or name.startswith("hermes_plugins.") or name == __name__


def _rebind_aliases(state: _TargetState, old: Iterable[Any], new_func: Callable[..., Any],
                    skip_modules: frozenset) -> set[tuple[str, str]]:
    olds = tuple(old)
    bound: set[tuple[str, str]] = set()
    for module in list(sys.modules.values()):
        if not isinstance(module, types.ModuleType) or module is state.owner or _skip_alias_module(module, skip_modules):
            continue
        try:
            items = list(vars(module).items())
        except Exception:  # health: allow BLE001 -- an exotic module object is simply skipped
            continue
        for alias, value in items:
            if any(value is o for o in olds):
                try:
                    setattr(module, alias, new_func)
                except Exception:  # health: allow BLE001 -- a read-only module attribute stays
                    continue
                bound.add((module.__name__, alias))
    return bound


def _install(target: str, state: _TargetState, new_func: Callable[..., Any], skip_modules: frozenset) -> bool:
    """Put ``new_func`` on the owner and rebind ``from x import f`` aliases by identity. False when
    something outside the registry replaced the attribute since we last installed it."""
    if state.foreign or vars(state.owner).get(state.attr, _MISSING) is not state.installed_raw:
        return False
    if new_func is state.installed_func:
        return True
    restoring = new_func is state.original_func
    new_raw = state.original_raw if restoring else (state.descriptor(new_func) if state.descriptor else new_func)
    setattr(state.owner, state.attr, new_raw)
    aliases = (_rebind_aliases(state, (state.installed_func, state.original_func), new_func, skip_modules)
               if state.is_function else set())
    _TARGETS[target] = replace(state, installed_raw=new_raw, installed_func=new_func,
                               aliases=frozenset() if restoring else frozenset(state.aliases | aliases))
    return True


def _abandon_target(target: str, state: _TargetState) -> None:
    """The attribute was replaced outside the registry: put our aliases back on the original and
    never touch this target again, so nothing the registry installed stays reachable through it."""
    if state.is_function and state.installed_func is not state.original_func:
        _rebind_aliases(state, (state.installed_func,), state.original_func, frozenset())
    _TARGETS[target] = replace(state, foreign=True, aliases=frozenset())


def _on_guard_failure(guard: shell.Guard, exc: BaseException, where: str, tripped: bool) -> None:
    """Called by a shell on a logged (rate limited) patch-layer failure, on the thread of the patched
    call: record the event, and on the failure that switches the patch off update the registry from a
    helper thread (the call never waits for the registry lock)."""
    from hermes_cli import core_patches_report as report

    report.event("auto-disabled" if tripped else "error", key=guard.key, detail=f"{where}: {guard.last_error}",
                 failures=guard.total)
    if tripped:
        detail = (f"switched off after {guard.consecutive} failures in a row (last: {guard.last_error}); the original "
                  f"runs. Reload the plugin or restart to retry")
        threading.Thread(target=disable_patch, args=(guard.key, detail), daemon=True,
                         name="core-patches-disable").start()


def _link(spec: CorePatchSpec, state: _TargetState) -> _Link:
    """The cached link of ``spec``; created (factory called) on first use only. The installed callable
    is the patch behind its safety shell (core_patches_shell.py)."""
    cached = _LINKS.get(spec.seq)
    if cached is not None:
        return cached
    flavor = shell.flavor_of(state.original_func)
    is_async = inspect.iscoroutinefunction(state.original_func)
    link = _Link(spec.key)
    original = state.original_func

    def inner() -> Callable[..., Any]:
        return link.inner if link.inner is not None else original

    guard = shell.Guard(spec.key, spec.kind, _SETTINGS.max_failures, spec.propagates, _on_guard_failure,
                        replace_fallback=_SETTINGS.replace_fallback)
    try:
        candidate = spec.fn if spec.kind == "replace" else spec.fn(shell.trampoline(guard, inner, original, flavor))
    except Exception as exc:  # health: allow BLE001 -- a broken factory skips its own patch only
        link.error = f"wrap factory raised {exc!r}"
    else:
        if not callable(candidate):
            link.error = "wrap factory returned a non-callable"
        elif inspect.iscoroutinefunction(candidate) != is_async:
            link.error = "async-ness differs from the target (async target needs an async function)"
        else:
            link.guard = guard
            link.wrapped = shell.make_shell(guard, candidate, inner, flavor, original=original)
    _LINKS[spec.seq] = link
    return link


def installed_patch(func: Any) -> Optional[str]:
    """The key of the patch whose callable ``func`` is (the registry never sets attributes on it)."""
    with _LOCK:
        return next((link.key for link in _LINKS.values() if link.wrapped is not None and link.wrapped is func), None)


# ── evaluation ───────────────────────────────────────────────────────────────────────────────


def _verdict(record: PatchRecord, probe: Optional[_Probe]) -> Optional[tuple[str, str]]:
    """(status, detail) for a record that cannot be installed, None when it is eligible."""
    live = [g for g in record.gens if g.published]
    spec = record.spec
    if not live:
        return PENDING, "waiting for the plugin's load to finish"
    undeclared = sorted({g.owner for g in live if not g.declared})
    if undeclared:
        return SKIPPED_ERROR, (f"{spec.name!r} is not listed under core_patches: in plugin.yaml "
                               f"(home {', '.join(undeclared)})")
    ungranted = sorted({g.owner for g in live if not g.granted})
    if ungranted:
        grant = f"plugins.entries.{spec.plugin_id}.allow_core_patch: true"
        if len(ungranted) == len({g.owner for g in live}):
            return SKIPPED_NO_GRANT, f"set {grant} to allow it"
        return SKIPPED_NO_GRANT, (f"a core patch is process-global: every home that loads {spec.plugin_id} must set "
                                  f"{grant}; missing in {', '.join(ungranted)}")
    if probe is None:
        return PENDING, "target module not imported yet; apply_core_patches installs it"
    if probe.error:
        return SKIPPED_ERROR, probe.error
    if probe.drift:
        return SKIPPED_DRIFT, f"{probe.drift}; see hermes plugins patches --diff {spec.name}"
    stale = next((g for g in live if g.spec.expect_ast_sha != probe.sha), None)
    if stale is not None:
        detail = (f"{spec.target} changed (expected {stale.spec.expect_ast_sha[:12]}, now "
                  f"{probe.sha[:12]}); see hermes plugins patches --diff {spec.name}")
        # spec 09: the hash differs but the target is located and is the code this process runs. With the default
        # on_drift "apply" the patch is installed anyway (status active, detail = the drift) and reported; the safety
        # shell is the net at call time. "skip" (per registration or core_patches.on_drift) is the old behaviour.
        if any((g.spec.on_drift or _SETTINGS.on_drift) == "skip" for g in live):
            return SKIPPED_DRIFT, detail
        return ACTIVE, detail + "; the plugin's patch runs anyway (on_drift apply)"
    return None


def _installable(verdict: Optional[tuple[str, str]]) -> bool:
    """None (clean) or (ACTIVE, drift text) (a drifted patch that runs): the patch goes into the chain."""
    return verdict is None or verdict[0] == ACTIVE


def _effective(gens: tuple, current: Optional[CorePatchSpec] = None) -> CorePatchSpec:
    """The registration whose fn is installed: the current one while it is still published (a second
    home joining does not swap the chain), else the newest published, else the newest staged."""
    published = [g for g in gens if g.published]
    if current is not None and any(g.spec.seq == current.seq for g in published):
        return current
    return max(published or gens, key=lambda g: g.spec.seq).spec


def _commit(target: str, changed: Iterable[str], probe: Optional[_Probe]) -> None:
    """Under the lock: re-evaluate the ``changed`` records on ``target`` (the others keep their
    status), then rebuild the chain from the active ones and install it."""
    changed = set(changed)
    on_target = [r for r in _RECORDS.values() if r.spec.target == target and r.gens]
    verdicts: dict[str, Optional[tuple[str, str]]] = {
        r.spec.key: (_verdict(r, probe) if r.spec.key in changed else
                     (ACTIVE, r.detail) if r.status == ACTIVE and r.detail else
                     None if r.status == ACTIVE else (r.status, r.detail))
        for r in on_target
    }
    for r in on_target:  # a patch the safety shell switched off stays off until its plugin reloads
        link = _LINKS.get(r.spec.seq)
        if _installable(verdicts[r.spec.key]) and link is not None and link.guard is not None and link.guard.disabled:
            verdicts[r.spec.key] = (DISABLED, r.detail if r.status == DISABLED else link.guard.last_error)
    replaces = sorted((r for r in on_target if _installable(verdicts[r.spec.key]) and r.spec.kind == "replace"),
                      key=lambda r: (r.status != ACTIVE, r.spec.seq))
    verdicts.update({r.spec.key: (SKIPPED_ERROR, f"{target} is already replaced by {replaces[0].spec.key}")
                     for r in replaces[1:]})
    members = sorted((r for r in on_target if _installable(verdicts[r.spec.key])),
                     key=lambda r: (r.spec.kind != "replace", r.spec.order, r.spec.seq))
    state = _target_state_or_error(target, members, verdicts)
    sha = probe.sha if probe is not None else ""
    if state is None:
        _apply_statuses(verdicts, sha)
        return
    current = state.original_func
    for record in members:
        link = _link(record.spec, state)
        if link.error:
            verdicts[record.spec.key] = (SKIPPED_ERROR, link.error)
            continue
        if record.spec.kind == "wrap":
            link.inner = current  # inner to outer: every intermediate chain is a valid one
        current = link.wrapped
    skip = frozenset(getattr(r.spec.fn, "__module__", "") or "" for r in on_target)
    if not _install(target, state, current, skip):
        if not state.foreign:
            _abandon_target(target, state)
        verdicts.update({r.spec.key: (SKIPPED_ERROR, f"{target} was replaced outside the registry; restart to recover")
                         for r in on_target})
    _apply_statuses(verdicts, sha)


def _target_state_or_error(target: str, members: list, verdicts: dict) -> Optional[_TargetState]:
    try:
        return _target_state(target)
    except Exception as exc:  # health: allow BLE001 -- recorded as the patches' status
        verdicts.update({r.spec.key: (SKIPPED_ERROR, f"target unresolvable: {exc}") for r in members})
        return None


def _apply_statuses(verdicts: dict, sha: str = "") -> None:
    for key, verdict in verdicts.items():
        status, detail = verdict if verdict is not None else (ACTIVE, "")  # (ACTIVE, text) = installed, drifted
        _set_status(key, status, detail, sha)


def _set_status(key: str, status: str, detail: str, sha: str = "") -> None:
    """Record a status (cheap, under the lock). Logging, events and notices happen in
    core_patches_report.publish, after the lock is released (``_after_change``)."""
    record = _RECORDS.get(key)
    if record is None:
        return
    _RECORDS[key] = replace(record, status=status, detail=detail, current_sha=sha or record.current_sha)


def _drop_link(seq: int) -> None:
    _LINKS.pop(seq, None)


# ── staging lifecycle (called by the ctx method and the plugin ledger) ───────────────────────


def stage(spec: CorePatchSpec, owner: str, *, granted: bool, declared: bool,
          declared_critical: bool = False, manager: Any = None) -> str:
    """Record a registration (cheap: no import, no hashing, nothing installed); returns its key.
    The same patch from another manager adds an owner; from the same owner (a reload) it is a new
    generation that replaces the old one when published."""
    spec = replace(spec, critical=True) if declared_critical and not spec.critical else spec
    if manager is not None:
        _MANAGERS.add(manager)
    with _LOCK:
        existing = _RECORDS.get(spec.key)
        others = [g for g in (existing.gens if existing else ()) if g.owner != owner]
        clash = next((g for g in others if (g.spec.target, g.spec.kind) != (spec.target, spec.kind)), None)
        if clash is not None:
            raise ValueError(f"core patch {spec.key} is already registered for {clash.spec.target} by {clash.owner}")
        gen = _Gen(owner=owner, spec=spec, granted=granted, declared=declared)
        mine = [g for g in (existing.gens if existing else ()) if g.owner == owner and g.published]
        gens = tuple(others + mine + [gen])
        _RECORDS[spec.key] = (PatchRecord(spec=_effective(gens), status=PENDING, gens=gens) if existing is None
                              else replace(existing, spec=_effective(gens, existing.spec), gens=gens))
        return spec.key


def _gen(record: Optional[PatchRecord], owner: str, seq: Optional[int], *, published: Optional[bool] = None):
    candidates = [g for g in (record.gens if record else ()) if g.owner == owner
                  and (seq is None or g.spec.seq == seq) and (published is None or g.published == published)]
    return max(candidates, key=lambda g: g.spec.seq) if candidates else None


def activate(key: str, owner: str, seq: Optional[int] = None) -> None:
    """The plugin's load succeeded: promote this generation (dropping the owner's older one) and
    swap the chain in one step under the lock. ``seq`` None = the owner's newest staged one."""
    refresh_settings()
    with _LOCK:
        record = _RECORDS.get(key)
        gen = _gen(record, owner, seq, published=False)
        if gen is None:
            return
        targets = {record.spec.target, gen.spec.target}
    probes = {t: _probe(t, may_import=bool(_EAGER) and not _on_load_worker()) for t in targets}
    with _LOCK:
        record = _RECORDS.get(key)
        if record is None or gen not in record.gens:
            return
        stale = [g for g in record.gens if g.owner == owner and g is not gen]
        gens = tuple([g for g in record.gens if g.owner != owner] + [replace(gen, published=True)])
        _RECORDS[key] = replace(record, spec=_effective(gens, record.spec), gens=gens)
        for old in stale:
            if not any(g.spec.seq == old.spec.seq for g in gens):
                _drop_link(old.spec.seq)
        for target in targets:
            _commit(target, {key} | _pending_on(target), probes[target])
    _after_change()


def _pending_on(target: str) -> set[str]:
    return {r.spec.key for r in _RECORDS.values() if r.spec.target == target and r.status == PENDING and r.gens}


def _remove_owner(key: str, owner: str, seq: Optional[int], *, tombstone: str) -> None:
    """Drop every generation of ``owner`` (when ``seq`` names one of them, or is None); the last
    owner removes the record, or leaves a tombstone for a critical patch when ``tombstone`` is set."""
    with _LOCK:
        record = _RECORDS.get(key)
        target = record.spec.target if record is not None and record.gens else None
    probe = _probe(target, may_import=False) if target and target in _TARGETS else None  # never under the lock
    with _LOCK:
        record = _RECORDS.get(key)
        if record is None:
            return
        if not record.gens:  # a tombstone: a later release of the failed generation clears it
            if seq is not None and record.spec.seq == seq and not tombstone:
                del _RECORDS[key]
            return
        if not any(g.owner == owner and (seq is None or g.spec.seq == seq) for g in record.gens):
            return
        dropped = [g for g in record.gens if g.owner == owner]
        gens = tuple(g for g in record.gens if g.owner != owner)
        was_active = record.status == ACTIVE
        for g in dropped:
            _drop_link(g.spec.seq)
        if gens:
            _RECORDS[key] = replace(record, spec=_effective(gens, record.spec), gens=gens)
        elif tombstone and record.critical:
            _RECORDS[key] = replace(record, gens=(), status=SKIPPED_ERROR,
                                    detail=f"plugin failed to load: {tombstone}")
        else:
            del _RECORDS[key]
        target = record.spec.target
        state = _TARGETS.get(target)
        if state is None:
            return
        _commit(target, {key} if gens else set(), probe)
        if was_active and _TARGETS.get(target, state).foreign:
            logger.warning("core patch %s: could not undo it on %s (replaced outside the registry); restart to remove",
                           key, target)
    _after_change()


def abort(key: str, owner: str, seq: int, error: Optional[str] = None) -> None:
    """The load that staged generation ``seq`` failed or timed out (its handle was disposed before
    publish). A published generation is unaffected (a routine unload-all)."""
    with _LOCK:
        gen = _gen(_RECORDS.get(key), owner, seq)
        if gen is None or gen.published:
            return
    _remove_owner(key, owner, None, tombstone=error or "register() raised or timed out after staging it")


def release(key: str, owner: str, seq: Optional[int] = None, *, failed: Optional[str] = None) -> None:
    """Undo (targeted unload, or a forced re-discovery the plugin did not come back from): drop
    ``owner``; the last owner removes the patch. ``failed``: the plugin's load error, if any (a
    critical patch then leaves a tombstone)."""
    _remove_owner(key, owner, seq, tombstone=failed or "")


_LOCK_WAIT_S = 1.0  # a hung wrap factory holds _LOCK for as long as it hangs: readers never wait longer


def _sorted_records(recs: Iterable[PatchRecord]) -> tuple[PatchRecord, ...]:
    return tuple(sorted(recs, key=lambda r: (r.spec.target, r.spec.order, r.spec.seq)))


def records() -> tuple[PatchRecord, ...]:
    return _snapshot_or_busy(_LOCK_WAIT_S)


def failure_stats() -> dict[str, tuple[int, int, str]]:
    """``{key: (failures in total, failures in a row, last error)}`` of every patch the safety shell guards."""
    links = list(_LINKS.values())  # atomic in CPython; never wait on _LOCK here
    return {link.key: (link.guard.total, link.guard.consecutive, link.guard.last_error)
            for link in links if link.guard is not None}


def _on_load_worker() -> bool:
    try:
        from hermes_cli.plugins_loader import in_plugin_load_worker

        return bool(in_plugin_load_worker())
    except Exception:  # health: allow BLE001 -- unknown: be conservative, do not import
        return True


# ── settings, switch-off, error records ─────────────────────────────────────────────────────


def refresh_settings() -> policy.Settings:
    """Re-read ``core_patches:`` from the config (never under the registry lock; never raises)."""
    global _SETTINGS
    _SETTINGS = policy.load_settings()
    try:
        from hermes_cli import core_patches_report as report

        report.configure(_SETTINGS)
    except Exception:  # health: allow BLE001
        logger.debug("core patches: could not configure the report module", exc_info=True)
    return _SETTINGS


def disable_patch(key: str, detail: str) -> None:
    """The safety shell switched ``key`` off: record it and rebuild the target's chain without it (the
    original is restored when it was the only patch). Never raises."""
    try:
        with _LOCK:
            record = _RECORDS.get(key)
            if record is None or record.status == DISABLED:
                return
            _RECORDS[key] = replace(record, status=DISABLED, detail=detail)
            target = record.spec.target
            if target in _TARGETS:
                _commit(target, set(), None)
        _after_change()
    except Exception:  # health: allow BLE001 -- the shell already passes calls to the original
        logger.warning("core patches: could not record that %s was switched off", key, exc_info=True)


def record_error(plugin_id: str, name: Any, target: Any, kind: Any, message: str, *, critical: bool = False) -> str:
    """Record a registration that could not even be staged (invalid arguments, a name clash) as a
    ``skipped-error`` tombstone, so it shows up in the table and the reports instead of vanishing.
    Never raises; returns the key."""
    key = f"{plugin_id}:{name}"
    try:
        spec = CorePatchSpec(plugin_id=str(plugin_id), name=str(name), target=str(target), kind=str(kind),
                             fn=lambda *a, **k: None, expect_ast_sha="0" * 64, order=0, critical=critical,
                             plugin_dir=None, seq=next(_SEQ))
        with _LOCK:
            if key not in _RECORDS:
                _RECORDS[key] = PatchRecord(spec=spec, status=SKIPPED_ERROR, detail=message, gens=())
    except Exception:  # health: allow BLE001
        logger.debug("core patches: could not record the registration error of %s", key, exc_info=True)
    return key


# ── refusal ─────────────────────────────────────────────────────────────────────────────────


def _refused(current: Iterable[PatchRecord]) -> list[str]:
    return [f"{r.spec.key} on {r.spec.target} is {r.status} ({r.detail})"
            for r in current if r.critical and r.status in REFUSE_STATUSES]


def _known_managers() -> list[Any]:
    try:
        import hermes_cli.plugins as plugins_mod
    except Exception:  # health: allow BLE001
        return list(_MANAGERS)
    found = list(getattr(plugins_mod, "_plugin_managers_by_home", {}).values())
    found += [getattr(plugins_mod, "_plugin_manager", None), *_MANAGERS]
    return [m for m in dict.fromkeys(m for m in found if m is not None)]


def load_attempted(manager: Any, loaded: Any) -> bool:
    """The plugin went through a real load (not a disabled / not-enabled placeholder)."""
    if getattr(loaded, "enabled", False):
        return True
    if not getattr(loaded, "error", None):
        return False
    try:
        from hermes_cli.plugins_discovery import _get_disabled_plugins, _get_enabled_plugins, gate_manifest
        from hermes_cli.plugins_loader import _plugin_home_scope

        with _plugin_home_scope(Path(manager.home_path)):
            verdict = gate_manifest(loaded.manifest, _get_disabled_plugins(), _get_enabled_plugins())
        return verdict.action in ("load", "load_now")
    except Exception:  # health: allow BLE001 -- cannot tell: the tombstone path still covers staged patches
        logger.debug("core patches: could not classify plugin %r", getattr(loaded.manifest, "name", "?"),
                     exc_info=True)
        return False


def _missing_entries() -> list[tuple[str, str, str]]:
    """(plugin key, patch name, why) of declared-critical names of enabled plugins that never registered them."""
    entries = []
    for manager in _known_managers():
        for plugin_key, loaded in list(getattr(manager, "_plugins", {}).items()):
            names = declared_critical_names(getattr(loaded.manifest, "path", None))
            missing = sorted(n for n in names if f"{plugin_key}:{n}" not in _RECORDS)
            if missing and load_attempted(manager, loaded):
                why = loaded.error or "register() did not call register_core_patch for it"
                entries += [(plugin_key, n, why) for n in missing]
    return entries


def _missing_declared() -> list[str]:
    """Declared-critical names (plugin.yaml) of enabled plugins that never registered them."""
    return [f"{plugin_key}:{n} is declared critical in plugin.yaml but not registered ({why})"
            for plugin_key, n, why in _missing_entries()]


def critical_problems(current: Optional[Iterable[PatchRecord]] = None) -> list[str]:
    """One line per critical patch that is not installed, with its detail (logs, refusal; never raises)."""
    try:
        return _refused(records() if current is None else current) + _missing_declared()
    except Exception:  # health: allow BLE001
        logger.debug("core patches: could not evaluate the critical patches", exc_info=True)
        return []


def _exception_type_of(last_error: str) -> str:
    """The exception type name at the head of a guard's ``last_error`` ("" when none)."""
    head = (last_error or "").split(": ", 1)[0]
    return head if re.fullmatch(r"[A-Za-z_][\w.]*", head) else ""


def critical_notice_lines(current: Optional[Iterable[PatchRecord]] = None) -> list[str]:
    """The home-channel form of ``critical_problems``: patch key, status and exception type name only (never a
    detail, a path or an exception message). Never raises."""
    try:
        stats = failure_stats()
        records_ = records() if current is None else current
        refused = [f"{r.spec.key} is {r.status}" + (f" ({kind})" if (kind := _exception_type_of(
                       stats.get(r.spec.key, (0, 0, ""))[2])) else "")
                   for r in records_ if r.critical and r.status in REFUSE_STATUSES]
        missing = [f"{plugin_key}:{n} is declared critical but not registered"
                   for plugin_key, n, _why in _missing_entries()]
        return refused + missing
    except Exception:  # health: allow BLE001
        logger.debug("core patches: could not build the critical notice", exc_info=True)
        return []


def _after_change() -> None:
    """After every change, outside the lock: report it (logs, status.json, events, notices), and after
    startup say that a critical patch dropped out (the process keeps running without it)."""
    try:
        from hermes_cli import core_patches_report as report

        report.publish(records(), surface="", settings=_SETTINGS)
        if _EAGER:
            report.after_startup_alert(_refused_after_startup(records()))
    except Exception:  # health: allow BLE001 -- reporting must never break a load, unload or call
        logger.debug("core patches: reporting failed", exc_info=True)


def _refused_after_startup(current: Iterable[PatchRecord]) -> list[str]:
    return [f"{r.spec.key} on {r.spec.target} is {r.status} ({r.detail})"
            for r in current if r.critical and r.status in LOUD_STATUSES]


def _install_deferred() -> None:
    """Install published patches whose target module was not imported yet (imports outside the lock)."""
    with _LOCK:
        targets = sorted({r.spec.target for r in _RECORDS.values()
                          if r.status == PENDING and any(g.published for g in r.gens)})
    for target in targets:
        probe = _probe(target, may_import=True)
        with _LOCK:
            _commit(target, _pending_on(target), probe)


# ── eager entry point ───────────────────────────────────────────────────────────────────────


def _apply_body(surface: str) -> None:
    """Discover plugins (installing their patches) and install the deferred ones. Never raises."""
    try:
        from hermes_cli.plugins import discover_plugins

        discover_plugins()
    except Exception:  # health: allow BLE001 -- discovery failure is reported where it happens
        logger.warning("core patches: plugin discovery failed (%s)", surface or "?", exc_info=True)
    _EAGER.add(surface or "?")
    try:
        _install_deferred()
    except Exception:  # health: allow BLE001 -- the patches stay pending; the core runs unpatched
        logger.warning("core patches: installing the deferred patches failed (%s)", surface or "?", exc_info=True)


def _snapshot_or_busy(wait_s: float) -> tuple[PatchRecord, ...]:
    """The records for the report: under the lock when it is free within ``wait_s``; else a shallow copy
    without it (the registry is busy, e.g. a hung factory), or () when even the copy fails."""
    if _LOCK.acquire(timeout=wait_s):
        try:
            return _sorted_records(_RECORDS.values())
        finally:
            _LOCK.release()
    logger.warning("core patches: registry busy, report skipped")
    try:
        return _sorted_records(list(_RECORDS.values()))
    except Exception:  # health: allow BLE001 -- the report of a busy registry is best effort
        return ()


def apply_core_patches(surface: str = "") -> tuple[PatchRecord, ...]:
    """Discover plugins now (installing their patches), within ``core_patches.apply_timeout_s``, and
    report what happened. Idempotent: safe to call from every entry point and more than once.

    Fail-safe: this never blocks startup longer than the budget (a slow discovery or import goes on in
    a daemon thread and its patches stay ``pending`` until it finishes) and never refuses it, except
    when ``core_patches.refuse_on_critical`` is true and a critical patch is not installed (exit 78).
    """
    settings = refresh_settings()
    worker = threading.Thread(target=contextvars.copy_context().run, args=(_apply_body, surface),
                              name="core-patches-apply", daemon=True)
    worker.start()
    worker.join(settings.apply_timeout_s)
    timed_out = worker.is_alive()
    if timed_out:
        logger.warning("core patches (%s): the %.1fs startup budget ran out; continuing without waiting (patches not "
                       "installed yet stay pending and install when discovery finishes)", surface or "?",
                       settings.apply_timeout_s)
        if _LOCK.acquire(timeout=min(_LOCK_WAIT_S, settings.apply_timeout_s)):
            try:
                for record in list(_RECORDS.values()):
                    if record.status == PENDING:
                        _set_status(record.spec.key, PENDING,
                                    f"apply budget of {settings.apply_timeout_s:g}s exceeded; "
                                    "installing in the background")
            finally:
                _LOCK.release()
    current = _snapshot_or_busy(min(_LOCK_WAIT_S, settings.apply_timeout_s))
    problems = critical_problems(current)
    try:
        from hermes_cli import core_patches_report as report

        report.publish(current, surface=surface or "?", settings=settings, summary=True,
                       wait_s=min(1.0, settings.apply_timeout_s))
        if problems and not settings.refuse_on_critical:
            report.critical_alert(problems, critical_notice_lines(current))
    except Exception:  # health: allow BLE001 -- reporting is best effort
        logger.debug("core patches: reporting failed (%s)", surface or "?", exc_info=True)
    if problems and settings.refuse_on_critical:
        message = "refusing to start: critical core patch " + "; ".join(problems)
        logger.error("core patches: %s", message)
        print(f"hermes: {message}", file=sys.stderr)
        raise CorePatchRefusal(message)
    return current


def _reset_for_tests() -> None:
    """Test-only: restore every patched target and forget all state."""
    global _SETTINGS
    with _LOCK:
        for target, state in list(_TARGETS.items()):
            if not state.foreign and vars(state.owner).get(state.attr, _MISSING) is state.installed_raw:
                _install(target, state, state.original_func, frozenset())
        for container in (_RECORDS, _TARGETS, _LINKS, _FILE_STATS, _SOURCES, _EAGER):
            container.clear()
        for manager in list(_MANAGERS):
            _MANAGERS.discard(manager)
    _SETTINGS = policy.Settings()
    try:
        from hermes_cli import core_patches_report as report

        report._reset_for_tests()
    except Exception:  # health: allow BLE001
        pass
