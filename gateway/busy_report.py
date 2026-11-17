"""What the gateway is doing right now: one report, by kind, that a restart decision can read.

A restart (``/restart``, SIGUSR1, the supervisor) interrupts or kills whatever is live. The core
restart wait (``gateway/restart_when_idle.py``) only holds for agent work it counts: turns, cron runs,
delegations and *notify-watched* background processes. A silent background process
(``terminal(background=true)`` without ``notify_on_complete``), a running ``execute_code`` cell and an
unanswered approval or clarify are not in that count, and ``stop()`` kills them (``process_registry
.kill_all(source="gateway_shutdown")``) or loses them (in-memory queues). This module lists every one of
those next to the counted work, so a caller can say "idle" with the same meaning a person would.

Shape of a report (``collect``)::

    {"generated_at": float, "busy": bool, "blocking": int, "uncertain": bool,
     "kinds": {kind: {"count": int, "blocking": int, "oldest_age_s": float | None,
                      "items": [{"id", "label", "age_s", "blocking", ...}]}},
     "errors": {section: "message"}}

``count`` is everything listed, ``blocking`` only what a restart would destroy or wait for (a wedged
turn, a restart-safe cron worker and an idle kernel are listed but not blocking). ``uncertain`` is true
when a section could not be read: callers must treat it as busy.

Plugins add sections with ``ctx.register_busy_probe(name, fn)`` (``hermes_cli/plugins_context.py``), which
lands in :func:`register_probe`. A probe is called on the gateway loop thread and must not block; it
returns an int, a list of item dicts, or ``{"items": [...]}``.

``bind_runner`` (called where the plugin message injector is published) lets code outside the loop thread
call :func:`collect_threadsafe`.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
import time
import weakref
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger("gateway.run")

# Built-in kinds, in display order; a probe's own name follows them.
KINDS = ("turns", "processes", "kernels", "cron", "delegations", "approvals", "clarifies")

_lock = threading.RLock()
_probes: dict[str, Callable[[], Any]] = {}
_runner_ref: Optional[weakref.ref] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
# First time an id was seen, for kinds whose source records no start time (approvals, clarifies).
_first_seen: dict[tuple[str, str], float] = {}


# ---------------------------------------------------------------------------------------------------------
# Runner binding and probes

def bind_runner(runner: Any) -> None:
    """Remember the live runner and the loop it runs on (called on the gateway loop at startup)."""
    global _runner_ref, _loop
    with _lock:
        _runner_ref = weakref.ref(runner)
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            _loop = getattr(runner, "_gateway_loop", None)


def unbind_runner(runner: Any) -> None:
    """Forget *runner* (a newer runner's binding is left alone)."""
    global _runner_ref, _loop
    with _lock:
        if _runner_ref is not None and _runner_ref() in (runner, None):
            _runner_ref, _loop = None, None


def current_loop() -> Optional[asyncio.AbstractEventLoop]:
    """The loop the bound runner runs on (``None`` when unbound)."""
    with _lock:
        return _loop


def current_runner() -> Any:
    """The bound runner, or ``None`` outside a gateway process or once it is stopping."""
    with _lock:
        return _runner_ref() if _runner_ref is not None else None


def register_probe(name: str, fn: Callable[[], Any]) -> Callable[[], None]:
    """Add (or replace) the busy probe *name*; returns the callable that removes it again."""
    if not callable(fn) or not str(name).strip():
        raise ValueError("a busy probe needs a name and a callable")
    key = str(name).strip()
    with _lock:
        _probes[key] = fn

    def release() -> None:
        with _lock:
            if _probes.get(key) is fn:
                del _probes[key]
    return release


def probe_names() -> list[str]:
    with _lock:
        return sorted(_probes)


# ---------------------------------------------------------------------------------------------------------
# Sections

def _item(item_id: Any, label: str = "", age_s: Optional[float] = None, blocking: bool = True, **extra: Any) -> dict:
    out = {"id": str(item_id), "label": str(label)[:120], "age_s": None if age_s is None else round(float(age_s), 1),
           "blocking": bool(blocking)}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def _seen_age(kind: str, item_id: str, now: float) -> float:
    with _lock:
        return now - _first_seen.setdefault((kind, item_id), now)


def _agent_timeout() -> float:
    try:
        return float(os.environ.get("HERMES_AGENT_TIMEOUT", 1800))
    except ValueError:
        return 1800.0


def _core_units(runner: Any) -> list[dict]:
    """The units the core restart wait counts (turns, cron, delegations, watched processes, notices)."""
    return list(runner._describe_active_work())


def _turn_items(units: list[dict]) -> list[dict]:
    timeout = _agent_timeout()
    items = []
    for unit in units:
        kind = unit.get("kind")
        if kind not in ("chat", "managed", "session", "mutation", "side_agent", "api", "deferred"):
            continue
        idle = unit.get("idle_s")
        wedged = bool(kind == "chat" and timeout > 0 and isinstance(idle, (int, float)) and idle >= timeout)
        ident = unit.get("session") or unit.get("task") or unit.get("thread") or f"{kind}:{unit.get('pid')}"
        items.append(_item(ident, kind, unit.get("elapsed_s"), blocking=not wedged, tool=unit.get("current_tool"),
                           idle_s=idle, wedged=wedged or None))
    return items


def _cron_items(units: list[dict]) -> list[dict]:
    return [_item(u.get("job_id"), "cron job", u.get("elapsed_s"),
                  blocking=not (u.get("wedged") or u.get("restart_safe")),
                  wedged=u.get("wedged") or None, restart_safe=u.get("restart_safe") or None)
            for u in units if u.get("kind") == "cron"]


def _delegation_items(units: list[dict]) -> list[dict]:
    items = [_item(u.get("delegation_id"), (u.get("goal") or "delegation"), u.get("elapsed_s"))
             for u in units if u.get("kind") == "delegation"]
    items += [_item(u.get("id"), "queued delegation notice") for u in units if u.get("kind") == "notice"]
    return items


def _process_items(units: list[dict]) -> list[dict]:
    """Every running registry process (silent ones included) plus a watched process whose exit notice is pending."""
    from tools.process_registry import process_registry
    items, listed = [], set()
    for entry in process_registry.list_sessions():
        if entry.get("status") != "running":
            continue
        listed.add(entry["session_id"])
        items.append(_item(entry["session_id"], entry.get("command", ""), entry.get("uptime_seconds"), pid=entry.get("pid"),
                           watched=bool(entry.get("notify_on_complete") or entry.get("watch_patterns")) or None,
                           persist_on_release=entry.get("persist_on_release") or None))
    items += [_item(u.get("process_id"), u.get("command", ""), u.get("elapsed_s"), exited=True)
              for u in units if u.get("kind") == "process" and u.get("process_id") not in listed and u.get("exited")]
    return items


def _kernel_items(now: float) -> list[dict]:
    """A kernel with a cell attached is running code; an idle one only holds state (listed, never blocking)."""
    from tools import code_kernel
    with code_kernel._REGISTRY.lock:
        kernels = list(code_kernel._KERNELS.values())
    mono = time.monotonic()
    return [_item(k.proc.pid if getattr(k, "proc", None) is not None else id(k), "execute_code kernel",
                  mono - k.last_used, blocking=k.attached > 0, cells=k.execution_count, running=k.attached > 0)
            for k in kernels]


def _approval_items(now: float) -> list[dict]:
    from tools import approval
    with approval._lock:
        queues = {key: list(queue) for key, queue in approval._gateway_queues.items() if queue}
    return [_item(e.data.get("request_id"), e.data.get("description") or e.data.get("command", ""),
                  _seen_age("approvals", str(e.data.get("request_id")), now), session=key)
            for key, queue in queues.items() for e in queue]


def _clarify_items(now: float) -> list[dict]:
    from tools import clarify_gateway
    with clarify_gateway._lock:
        entries = list(clarify_gateway._entries.values())
    return [_item(e.clarify_id, e.question, _seen_age("clarifies", e.clarify_id, now), session=e.session_key)
            for e in entries if e.response is None]


def _probe_items(fn: Callable[[], Any]) -> list[dict]:
    got = fn()
    if isinstance(got, dict):
        got = got.get("items", [])
    if isinstance(got, bool) or got is None:
        return [_item("probe", "busy")] if got else []
    if isinstance(got, int):
        return [_item(f"#{i + 1}") for i in range(got)]
    return [_item(**({"item_id": x.get("id", "?"), **{k: v for k, v in x.items() if k != "id"}}))
            if isinstance(x, dict) else _item(str(x)) for x in (got if isinstance(got, Iterable) else [])]


def _section(items: list[dict]) -> dict:
    ages = [i["age_s"] for i in items if i.get("age_s") is not None]
    return {"count": len(items), "blocking": sum(1 for i in items if i["blocking"]),
            "oldest_age_s": max(ages) if ages else None, "items": items}


def collect(runner: Any = None, now: Optional[float] = None) -> dict:
    """The busy report. Call on the loop thread (or through :func:`collect_threadsafe`)."""
    runner = runner if runner is not None else current_runner()
    now = time.time() if now is None else now
    errors: dict[str, str] = {}
    units: list[dict] = []
    if runner is not None:
        try:
            units = _core_units(runner)
        except Exception as exc:  # health: allow BLE001 -- a source that cannot be read makes the report uncertain
            errors["core_units"] = f"{type(exc).__name__}: {exc}"
    sections: dict[str, Callable[[], list[dict]]] = {
        "turns": lambda: _turn_items(units), "processes": lambda: _process_items(units),
        "kernels": lambda: _kernel_items(now), "cron": lambda: _cron_items(units),
        "delegations": lambda: _delegation_items(units), "approvals": lambda: _approval_items(now),
        "clarifies": lambda: _clarify_items(now)}
    with _lock:
        probes = dict(_probes)
    kinds: dict[str, dict] = {}
    for name, build in (*sections.items(), *((n, (lambda f=f: _probe_items(f))) for n, f in sorted(probes.items()))):
        try:
            kinds[name] = _section(build())
        except Exception as exc:  # health: allow BLE001 -- one broken section must not hide the others
            errors[name] = f"{type(exc).__name__}: {exc}"
            kinds[name] = _section([])
    with _lock:  # ids that are gone must not keep an age for a later namesake
        live = {(k, i["id"]) for k, s in kinds.items() for i in s["items"]}
        for key in [k for k in _first_seen if k not in live]:
            del _first_seen[key]
    blocking = sum(s["blocking"] for s in kinds.values())
    return {"generated_at": now, "busy": blocking > 0 or bool(errors), "blocking": blocking,
            "uncertain": bool(errors), "kinds": kinds, "errors": errors}


def collect_threadsafe(timeout: float = 5.0) -> dict:
    """:func:`collect` run on the gateway loop from any thread. No runner: an idle, empty report. A loop that does
    not answer within *timeout* is reported as uncertain (busy): a stalled gateway is not an idle one."""
    runner = current_runner()
    with _lock:
        loop = _loop
    if runner is None or loop is None or loop.is_closed():
        return {"generated_at": time.time(), "busy": False, "blocking": 0, "uncertain": False, "kinds": {},
                "errors": {}, "no_runner": True}
    try:
        if asyncio.get_running_loop() is loop:
            return collect(runner)
    except RuntimeError:
        pass
    done: concurrent.futures.Future = concurrent.futures.Future()

    def _run() -> None:
        try:
            done.set_result(collect(runner))
        except Exception as exc:  # health: allow BLE001 -- surface to the waiting thread
            done.set_exception(exc)
    try:
        loop.call_soon_threadsafe(_run)
        return done.result(timeout=timeout)
    except Exception as exc:  # health: allow BLE001 -- timeout, closed loop, collect failure: all "cannot tell"
        return {"generated_at": time.time(), "busy": True, "blocking": 0, "uncertain": True, "kinds": {},
                "errors": {"loop": f"{type(exc).__name__}: {exc}" or "no answer"}}


# ---------------------------------------------------------------------------------------------------------
# Rendering

def _age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else (f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s")


def format_report(report: dict, *, limit: int = 6) -> str:
    """Human-readable lines for a chat reply or a terminal (item labels are user content: for people, not logs)."""
    kinds = report.get("kinds", {})
    if not report.get("busy"):
        return "idle: nothing is running" + (" (gateway not bound)" if report.get("no_runner") else "")
    lines = [f"busy: {report.get('blocking', 0)} blocking item(s)" + (" (some sources unreadable)" if report.get("uncertain") else "")]
    for kind, section in kinds.items():
        if not section["count"]:
            continue
        oldest = f", oldest {_age(section['oldest_age_s'])}" if section["oldest_age_s"] is not None else ""
        lines.append(f"{kind}: {section['blocking']} blocking of {section['count']}{oldest}")
        for it in section["items"][:limit]:
            lines.append(f"  - {'' if it['blocking'] else '(not blocking) '}{it['id'][:24]} {it['label'][:60]} [{_age(it['age_s'])}]".rstrip())
        if section["count"] > limit:
            lines.append(f"  ... {section['count'] - limit} more")
    lines += [f"unreadable {name}: {msg}" for name, msg in report.get("errors", {}).items()]
    return "\n".join(lines)


def log_view(report: dict) -> str:
    """Kinds, ids and ages only (labels and commands are user content: never in a log line)."""
    parts = [f"{kind}[{s['blocking']}/{s['count']}]: " + ", ".join(f"{i['id'][:16]}@{_age(i['age_s'])}" for i in s["items"][:8])
             for kind, s in report.get("kinds", {}).items() if s["count"]]
    return "; ".join(parts) or "nothing"
