"""Generic seams for "restart only when idle": the restart request, the marker file and the notices.

The policy (when is the gateway idle enough, drain mode, deadlines) lives in a plugin; this file is what
a plugin, ``hermes gateway restart --when-idle`` and shell tooling share.

* :func:`request_graceful_restart` is the ONE way to start the gateway's own graceful restart from outside
  the loop: the same path as ``/restart`` (``GatewayRunner.request_restart``; exit 75 under a supervisor, so
  launchd or systemd relaunches it), after logging what is running at that moment. Callable from any thread.
* The marker ``<hermes home>/restart-requested.json`` is how an external tool asks for a deferred restart::

      {"requested_at": <epoch s>, "reason": str, "mode": "wait" | "drain", "max_wait": <s> | null,
       "requested_by": str}

  ``wait`` serves normally until an idle moment; ``drain`` also holds new inbound turns. A consumer adds
  ``fired_at`` and ``fired_by_pid`` right before it asks for the restart, so the next process knows the
  request was carried out (a different pid) and removes it.
* ``<hermes home>/graceful-restart/status.json`` is the consumer's heartbeat (``heartbeat_at``, ``pid``,
  ``phase``) and ``busy.txt`` its last busy report as text: tooling that wants to know whether a consumer
  is alive, or what blocks, reads those and never needs to reach the gateway.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from gateway import busy_report

logger = logging.getLogger("gateway.run")

MARKER_NAME = "restart-requested.json"
STATE_DIR = "graceful-restart"
MODES = ("wait", "drain")
# A consumer that has not written its heartbeat for this long is not alive (its poll is every few seconds).
HEARTBEAT_MAX_AGE_S = 45.0

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


# ---------------------------------------------------------------------------------------------------------
# Marker and heartbeat files

def _home(home: Optional[Path] = None) -> Path:
    if home is not None:
        return Path(home)
    from hermes_constants import get_hermes_home
    return get_hermes_home()


def marker_path(home: Optional[Path] = None) -> Path:
    return _home(home) / MARKER_NAME


def state_dir(home: Optional[Path] = None) -> Path:
    return _home(home) / STATE_DIR


def status_path(home: Optional[Path] = None) -> Path:
    return state_dir(home) / "status.json"


def busy_text_path(home: Optional[Path] = None) -> Path:
    return state_dir(home) / "busy.txt"


def parse_duration(text: Any) -> Optional[float]:
    """``"6h"``, ``"90m"``, ``"45s"``, ``"2d"`` or bare seconds -> seconds; ``None``/``""``/``0``/``none`` -> ``None`` (no limit)."""
    if text is None or str(text).strip().lower() in ("", "0", "none", "off", "never"):
        return None
    match = _DURATION.match(str(text))
    if not match:
        raise ValueError(f"not a duration: {text!r} (use 45s, 90m, 6h, 2d)")
    seconds = float(match.group(1)) * _UNITS[match.group(2).lower()]
    return seconds if seconds > 0 else None


def write_marker(*, reason: str = "", mode: str = "wait", max_wait: Optional[float] = None, requested_by: str = "",
                 home: Optional[Path] = None, now: Optional[float] = None) -> dict:
    """Record a deferred restart request (replaces an earlier one). Returns what was written."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    marker = {"requested_at": time.time() if now is None else now, "reason": str(reason)[:300], "mode": mode,
              "max_wait": max_wait, "requested_by": str(requested_by)[:80]}
    from utils import atomic_json_write
    _home(home).mkdir(parents=True, exist_ok=True)
    atomic_json_write(marker_path(home), marker, indent=2)
    return marker


def read_marker(home: Optional[Path] = None) -> Optional[dict]:
    """The pending request, or ``None`` when absent or not a valid marker (an unreadable one is logged)."""
    path = marker_path(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable %s: %s", path, exc)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("requested_at"), (int, float)):
        logger.warning("Ignoring %s: no numeric requested_at", path)
        return None
    max_wait = data.get("max_wait")
    return {**data, "reason": str(data.get("reason") or ""),
            "mode": data.get("mode") if data.get("mode") in MODES else "wait",
            "max_wait": float(max_wait) if isinstance(max_wait, (int, float)) and max_wait > 0 else None}


def clear_marker(home: Optional[Path] = None) -> bool:
    """Remove the marker; True when one existed."""
    try:
        marker_path(home).unlink()
        return True
    except FileNotFoundError:
        return False


def read_status(home: Optional[Path] = None) -> Optional[dict]:
    try:
        data = json.loads(status_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def consumer_alive(home: Optional[Path] = None, *, now: Optional[float] = None) -> bool:
    """True when a marker consumer's heartbeat is fresh and its process still exists."""
    status = read_status(home)
    if not status or not isinstance(status.get("heartbeat_at"), (int, float)):
        return False
    if (time.time() if now is None else now) - status["heartbeat_at"] > HEARTBEAT_MAX_AGE_S:
        return False
    pid = status.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # windows-footgun: ok -- the gateway restart tooling is POSIX; a missing pid just reads as dead
    except ProcessLookupError:
        return False
    except OSError:
        pass  # exists, owned by someone else
    return True


# ---------------------------------------------------------------------------------------------------------
# Running on the gateway loop

def _on_loop(fn: Callable[[Any], Any], *, timeout: float = 5.0, default: Any = None) -> Any:
    """Run ``fn(runner)`` on the gateway loop from any thread; *default* when no gateway is bound or it does not answer."""
    runner, loop = busy_report.current_runner(), busy_report.current_loop()
    if runner is None or loop is None or loop.is_closed():
        return default
    try:
        if asyncio.get_running_loop() is loop:
            return fn(runner)
    except RuntimeError:
        pass
    done: concurrent.futures.Future = concurrent.futures.Future()

    def _run() -> None:
        try:
            done.set_result(fn(runner))
        except Exception as exc:  # health: allow BLE001 -- handed to the waiting thread
            done.set_exception(exc)
    try:
        loop.call_soon_threadsafe(_run)
        return done.result(timeout=timeout)
    except Exception:  # health: allow BLE001 -- timeout or closed loop: the caller keeps its default
        logger.warning("graceful restart: the gateway loop did not run the request", exc_info=True)
        return default


def request_graceful_restart(reason: str, *, timeout: float = 5.0) -> bool:
    """Start the gateway's own graceful restart, like ``/restart``; True when one is now in progress.

    The what-is-running snapshot goes to the log first: a restart must never lose work silently, and the
    resumable pieces (interrupted turns, delegations) are recovered by the existing restart machinery.
    """
    from gateway.restart import is_container_restart_context, is_gateway_supervisor_process
    via_service = is_gateway_supervisor_process() or is_container_restart_context()

    def _go(runner: Any) -> bool:
        logger.warning("Graceful restart requested (%s). Running at this moment: %s", reason,
                       busy_report.log_view(busy_report.collect(runner)))
        started = runner.request_restart(detached=not via_service, via_service=via_service)
        return bool(started or getattr(runner, "_restart_requested", False))
    return bool(_on_loop(_go, timeout=timeout, default=False))


def notify_home(text: str, *, timeout: float = 10.0) -> int:
    """Send *text* to every served profile's home channel; returns how many sends were delivered. Never raises."""
    async def _send(runner: Any) -> int:
        sent = 0
        for profile, platform, cfg in runner._served_home_channel_configs():
            home_channel = getattr(cfg, "home_channel", None)
            adapter = runner._authorization_adapter(platform, profile) if home_channel and home_channel.chat_id else None
            if adapter is None:
                continue
            meta = runner._thread_metadata_for_target(platform, str(home_channel.chat_id), home_channel.thread_id,
                                                      adapter=adapter)
            sent += bool(await runner._send_notice_logged(
                adapter, str(home_channel.chat_id), text, platform.value,
                "graceful restart: notice to %s:%s failed: %s", metadata=meta))
        return sent
    return _spawn_coro(_send, timeout=timeout)


def notify_source(source: Any, text: str, *, timeout: float = 10.0) -> bool:
    """Reply *text* to the chat *source* (a ``SessionSource``) came from. Never raises."""
    async def _send(runner: Any) -> int:
        adapter = runner._delivery_adapter_for(source)
        if adapter is None:
            return 0
        meta = runner._thread_metadata_for_target(source.platform, str(source.chat_id), source.thread_id,
                                                  chat_type=getattr(source, "chat_type", None), adapter=adapter)
        return int(bool(await runner._send_notice_logged(
            adapter, str(source.chat_id), text, source.platform.value,
            "graceful restart: notice to %s:%s failed: %s", metadata=meta)))
    return bool(_spawn_coro(_send, timeout=timeout))


def _spawn_coro(make: Callable[[Any], Any], *, timeout: float) -> int:
    runner, loop = busy_report.current_runner(), busy_report.current_loop()
    if runner is None or loop is None or loop.is_closed():
        return 0
    try:
        try:
            on_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False
        if on_loop:  # waiting here would stall the very loop that has to send it: schedule and go on
            loop.create_task(make(runner))
            return 1
        future = asyncio.run_coroutine_threadsafe(make(runner), loop)
        return int(future.result(timeout=timeout) or 0)
    except Exception:  # health: allow BLE001 -- a notice is best effort
        logger.warning("graceful restart: could not send a notice", exc_info=True)
        return 0


# ---------------------------------------------------------------------------------------------------------
# `hermes gateway restart --when-idle`

def cmd_restart_when_idle(args: Any) -> None:
    """Write the marker and return at once: nothing is stopped, the running gateway decides when to restart."""
    mode = "drain" if getattr(args, "drain", False) else "wait"
    max_wait = parse_duration(getattr(args, "max_wait", None))
    marker = write_marker(reason=getattr(args, "reason", None) or "hermes gateway restart --when-idle", mode=mode,
                          max_wait=max_wait, requested_by="cli")
    print(f"Restart requested for the next idle moment (mode={mode}, max wait "
          f"{'none' if max_wait is None else f'{max_wait:.0f}s'}): {marker_path()}")
    if not consumer_alive():
        print("Warning: no graceful-restart plugin heartbeat. The request waits in the marker file until a gateway "
              "that runs the plugin picks it up; `hermes gateway restart` (without --when-idle) still restarts "
              "when idle on the core path, but kills background processes it does not count.")
        return
    try:
        print(busy_text_path().read_text(encoding="utf-8").rstrip() or "idle: nothing is running")
    except OSError:
        print("(no busy report yet)")
