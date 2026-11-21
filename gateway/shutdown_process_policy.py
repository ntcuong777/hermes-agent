"""Shutdown policy for persisted background processes (#41225 family).

Upstream contract, the default while no plugin registers a policy: the
gateway's stop kills every running tool subprocess, ``persist_on_release``
jobs included (``gateway/run_shutdown.py``, ``kill_all(source=
"gateway_shutdown")``), because a dead gateway would otherwise leave
unsupervised PPID=1 orphans (#41225/#46778).

A plugin may register a policy and ask for the crash contract instead: leave
``persist_on_release`` host processes running across a clean shutdown. They
are already checkpointed (``tools/process_registry_checkpoint.py`` writes on
every spawn/exit) and the next gateway re-adopts them through
``recover_from_checkpoint()`` into the same session — owner task, watcher
routes and notification routing all carry over. Adoption at the next startup
is the answer to the orphan rationale: the unsupervised window lasts only
until the service comes back.

The policy applies to every clean shutdown, not to a "restart" flag:
``launchctl kickstart -k`` (``hgr``), a rebuild's launchd reload and
``hermes gateway restart`` all deliver SIGTERM without the restart-when-idle
flag, so flag-scoping would miss the restarts that matter. It does not touch
explicit operator stops (``process.kill``, CLI ``/stop`` pass their own source
and still reach persisted jobs), the ``hermes update --force`` checkpoint
sweep, or env-backed (VM/container) processes whose environment teardown
remains upstream's.

A policy fn takes no arguments and answers truthy/falsy; a raise counts as
"no" so a broken plugin degrades to the upstream kill (fail closed).
"""

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_policies: dict[str, Callable[[], Any]] = {}
_lock = threading.RLock()


def register_policy(key: str, fn: Callable[[], Any]) -> Callable[[], None]:
    """Add (or replace) the shutdown policy *key*; returns the callable that removes it again."""
    if not callable(fn) or not str(key).strip():
        raise ValueError("a shutdown process policy needs a key and a callable")
    name = str(key).strip()
    with _lock:
        _policies[name] = fn

    def release() -> None:
        with _lock:
            if _policies.get(name) is fn:
                del _policies[name]
    return release


def policy_keys() -> list[str]:
    with _lock:
        return sorted(_policies)


def _keep_asked_for() -> bool:
    """True when any registered policy asks to keep persisted processes; a raise is a 'no'."""
    with _lock:
        fns = list(_policies.values())
    for fn in fns:
        try:
            if fn():
                return True
        except Exception:
            # Fail closed to the upstream kill — but a broken plugin must be visible,
            # or the operator only notices when a restart silently killed their jobs.
            logger.warning("shutdown process policy %r raised; treating as 'no' (kill)", fn, exc_info=True)
    return False


def persisted_spawn_log(session_id: str) -> Optional[str]:
    """The output log a ``persist_on_release`` host spawn should write to, or ``None`` for
    the upstream PIPE. A pipe dies with the gateway (the child's next write is SIGPIPE),
    and a PTY dies with the gateway's master fd — so a job meant to survive the gateway,
    like one kept at shutdown, must be log-backed from the start (the crash contract
    already re-adopts log-backed sessions; worker spawns do the same via
    ``worker_output_log``). ``None`` unless a registered policy asks to keep: upstream
    behavior is untouched when no plugin is loaded."""
    if not _keep_asked_for():
        return None
    from tools.process_registry_output_log import make_session_log
    return make_session_log(session_id)


def adoptable_keep_ids(registry: Any) -> frozenset[str]:
    """The ids of running persisted host processes to leave alive at shutdown, for
    ``kill_all(exclude_ids=...)``. Empty unless a registered policy asks to keep:
    the sweep then behaves exactly as upstream. The filter mirrors the checkpoint
    writer (``not s.exited and not s.sandbox_key``): only what the checkpoint
    persists can be re-adopted, and an in-sandbox PID means nothing once its
    environment is gone; ``pid_scope == "host"`` additionally matches what
    ``recover_from_checkpoint`` accepts."""
    if not _keep_asked_for():
        return frozenset()
    with registry._lock:
        return frozenset(
            s.id for s in registry._running.values()
            if not s.exited and s.persist_on_release and not s.sandbox_key and s.pid_scope == "host"
        )
