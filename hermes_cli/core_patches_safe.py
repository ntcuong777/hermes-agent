"""One-line call sites of the core-patch registry in upstream-owned files (fork-owned).

STDLIB ONLY at module level, so importing this never fails because an upstream name moved. Every
helper swallows ``Exception`` and any stray ``SystemExit`` (logged), so a broken fork module or an
upstream rename can never break ``hermes`` (``hermes doctor`` and ``hermes update`` included). The one
thing that can propagate is :class:`hermes_cli.core_patches.CorePatchRefusal` (a ``SystemExit``), and
only while ``core_patches.refuse_on_critical: true`` (default false): ``apply`` re-checks the flag
itself, so a refusal raised without it is logged and dropped. ``cron_dispatch_guard`` is a no-op
unless the flag is on.
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("hermes_cli.core_patches")

CRON_REFUSAL_FILE = "core-patch-refusal.json"
CRON_REFUSAL_COOLDOWN_SECONDS = 600.0


def _is_refusal(exc: BaseException) -> bool:
    return isinstance(exc, SystemExit) and isinstance(getattr(exc, "message", None), str)


def refusal_enabled() -> bool:
    """``core_patches.refuse_on_critical`` is true in the current config (default false; never raises)."""
    try:
        return bool(importlib.import_module("hermes_cli.core_patches_policy").load_settings().refuse_on_critical)
    except Exception:  # health: allow BLE001 -- unreadable settings never refuse
        return False


def call(target: str, *args: Any, **kwargs: Any) -> Any:
    """Run fork-owned ``module:function``; any Exception (and a stray SystemExit) is logged and None
    returned. A CorePatchRefusal propagates (``apply`` decides whether it may)."""
    module_name, _, func_name = target.partition(":")
    try:
        return getattr(importlib.import_module(module_name), func_name)(*args, **kwargs)
    except KeyboardInterrupt:
        raise
    except SystemExit as exc:
        if _is_refusal(exc):
            raise
        logger.warning("core patches: %s called sys.exit(%r) (ignored)", target, exc.code, exc_info=True)
        return None
    except Exception:  # health: allow BLE001 -- a fork module must never break an upstream command
        logger.warning("core patches: %s failed (ignored)", target, exc_info=True)
        return None


def apply(surface: str) -> Any:
    """``apply_core_patches(surface)``. Never raises, except the refusal (SystemExit 78) while
    ``core_patches.refuse_on_critical`` is true."""
    try:
        return call("hermes_cli.core_patches:apply_core_patches", surface)
    except SystemExit as refusal:
        if not (_is_refusal(refusal) and refusal_enabled()):
            logger.error("core patches: a refusal was raised but core_patches.refuse_on_critical is not true; "
                         "starting anyway (%s)", getattr(refusal, "message", refusal))
            return None
        raise


def add_patches_parser(plugins_subparsers: Any) -> None:
    call("hermes_cli.core_patches_cmd:add_patches_parser", plugins_subparsers)


def cmd_patches(args: Any) -> None:
    """``hermes plugins patches``: an unusable registry is a clear error and exit 1, not a traceback."""
    try:
        command = importlib.import_module("hermes_cli.core_patches_cmd").cmd_patches
    except Exception as exc:  # health: allow BLE001
        print(f"hermes plugins patches: the core-patch registry is unavailable ({exc!r})", file=sys.stderr)
        raise SystemExit(1) from None
    command(args)


def doctor_check(should_fix: bool) -> Any:
    """The ``hermes doctor`` row; on any failure an empty Finding (doctor goes on)."""
    try:
        return importlib.import_module("hermes_cli.core_patches_cmd")._check_core_patches(should_fix)
    except Exception:  # health: allow BLE001
        logger.warning("core patches: doctor row failed (ignored)", exc_info=True)
        from hermes_cli.doctor_report import Finding

        return Finding()


# ── gateway: refuse like the other startup refusals ─────────────────────────────────────────


def gateway_apply() -> None:
    """Gateway startup. Normally just ``apply("gateway")`` (nothing refuses). Only with
    ``core_patches.refuse_on_critical: true``: on a refusal publish ``gateway_state=startup_failed``
    with the reason (the same status helper the access-policy and multiplex refusals use) and exit
    78, which ``gateway.run_bootstrap`` propagates like ``GATEWAY_FATAL_CONFIG_EXIT_CODE``."""
    try:
        apply("gateway")
    except SystemExit as refusal:
        if isinstance(getattr(refusal, "message", None), str):  # a CorePatchRefusal
            _gateway_startup_failed(refusal.message)
        raise


def _gateway_startup_failed(reason: str) -> None:
    try:
        from gateway.run import _write_runtime_status_quiet

        _write_runtime_status_quiet(gateway_state="startup_failed", exit_reason=f"core_patch_refusal: {reason}")
    except Exception:  # health: allow BLE001 -- the refusal itself must still happen
        logger.warning("core patches: could not publish the startup_failed state", exc_info=True)


# ── cron external worker: refuse once, then do not respawn within a cooldown ───────────────


def _refusal_path(home: Optional[Path] = None) -> Path:
    if home is None:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    return Path(home) / "cron" / CRON_REFUSAL_FILE


def cron_apply() -> None:
    """Cron external worker. Normally just ``apply("cron")`` (nothing refuses, no marker is written).
    Only with ``core_patches.refuse_on_critical: true``: a refusal records itself under the owning
    profile's home (the parent reads it before the next spawn), then exits 78 before the ownership
    acknowledgement, so the parent marks that run failed once with the refusal on stderr."""
    try:
        apply("cron")
    except SystemExit as refusal:
        if not _is_refusal(refusal):
            raise
        try:
            path = _refusal_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            now = time.time()
            path.write_text(json.dumps({"at": now, "until": now + CRON_REFUSAL_COOLDOWN_SECONDS,
                                        "message": refusal.message}) + "\n", encoding="utf-8")
        except Exception:  # health: allow BLE001
            logger.warning("core patches: could not record the cron refusal", exc_info=True)
        raise


def cron_dispatch_guard(home: Optional[Path] = None) -> None:
    """Before spawning a cron external worker. A no-op unless ``core_patches.refuse_on_critical`` is
    true (a marker left by an older version is ignored). With it: raise RuntimeError (the caller records
    a dispatch failure, no process is spawned) while a worker's refusal is younger than the cooldown."""
    try:
        data = json.loads(_refusal_path(home).read_text(encoding="utf-8"))
        until, message = float(data["until"]), str(data.get("message", ""))
    except Exception:  # health: allow BLE001 -- no (or unreadable) marker: dispatch normally
        return
    if not refusal_enabled():
        return
    remaining = until - time.time()
    if remaining > 0:
        raise RuntimeError(f"a cron worker refused to start (critical core patch); not spawning another for "
                           f"{remaining:.0f}s: {message}")
