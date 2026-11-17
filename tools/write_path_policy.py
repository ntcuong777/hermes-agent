"""``resolve_write_policy``: plugins vote on the paths write_file / patch would write (fork-owned).

Called once per write check from ``tools/file_tools_write_guards.py::_check_sensitive_path`` in place
of upstream's system-prefix test. Each candidate path of the write (its resolved realpath and its
normalized expansion, the two upstream checks) goes to every ``resolve_write_policy`` callback with
``path``, ``requested_path`` (as the tool was given it), ``op`` (``"write"``), ``task_id`` and
``system_prefix`` (whether upstream's prefix list, ``/etc/``, ``/private/etc/``, ..., covers it).

A callback returns ``"allow"``, ``"deny"`` or ``None`` (no opinion). The write is refused when ANY
callback answers anything but ``"allow"``/``None`` for any candidate: ``"deny"``, a malformed value,
or the block directive the plugin manager substitutes for a callback that raised or timed out (the
hook is in ``_HOOK_TIMEOUT_FAIL_CLOSED_HOOKS``). Otherwise a candidate under a system prefix is
allowed only when the ``config-seams`` plugin said ``"allow"`` for it (``tools/hook_sources.py``: any
plugin can register the hook, so an ``"allow"`` from another plugin counts as no opinion, while its
``"deny"`` still wins): ``"allow"`` lifts the PREFIX rule and nothing else (upstream's exact paths,
the Hermes config guard, the protected-instruction and approval gates and the sandbox still apply).
With no callback registered the result is upstream's verdict.

Every call carries a fresh ``tool_call_id``: the bounded-worker path gates concurrent calls of one
callback on that field, so without it a second write arriving while the first callback ran would be
skipped, which a fail-closed hook turns into a deny of an unrelated path.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Sequence

from tools.hook_sources import attributed

HOOK = "resolve_write_policy"
ALLOW, DENY = "allow", "deny"


def _verdicts(candidate: str, requested_path: str, task_id: str, system_prefix: bool) -> list[tuple[bool, Any]]:
    return attributed(HOOK, path=candidate, requested_path=requested_path, op="write", task_id=task_id,
                      system_prefix=system_prefix, tool_call_id=uuid.uuid4().hex)


def _candidate_denied(candidate: str, key: str, requested_path: str, task_id: str, prefixes: tuple) -> bool:
    system_prefix = key.startswith(prefixes)
    verdicts = _verdicts(candidate, requested_path, task_id, system_prefix)
    if any(verdict not in (ALLOW, None) for _, verdict in verdicts):
        return True
    return system_prefix and not any(trusted and verdict == ALLOW for trusted, verdict in verdicts)


def system_path_denied(requested_path: str, candidates: Sequence[str], keys: Iterable[str], prefixes: tuple,
                       task_id: str) -> bool:
    """Upstream's ``any(k.startswith(prefixes) for k in keys)``, decided with the hook when one is registered."""
    keys = list(keys)
    try:
        from hermes_cli.plugins import has_hook

        hooked = has_hook(HOOK)
    except Exception:  # health: allow BLE001 -- no plugin system: upstream's verdict
        hooked = False
    if not hooked:
        return any(k.startswith(prefixes) for k in keys)
    return any(_candidate_denied(c, k, requested_path, task_id, prefixes) for c, k in zip(candidates, keys))
