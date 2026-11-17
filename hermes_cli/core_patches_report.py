"""Reporting of the core-patch registry: logs, status.json, events.jsonl, home-channel notices (fork-owned).

Everything here is best effort: it never raises, never blocks start (``publish(wait_s=...)`` bounds
the file writes of the startup path) and a failure to write is a debug line. Files live under
``<hermes home>/core-patches/``:

* ``status.json``  : the patches as the latest process saw them (pid, surface, fork head, upstream
  ref, per patch status, reason, recorded and current hash, recorded upstream commit, last_changed).
  Written atomically at each apply and whenever the set or a status changes.
* ``events.jsonl`` : append-only, one JSON object per line: applied, drift-first-seen,
  drift-recovered, error, auto-disabled, critical-skipped, skipped. Rotates to ``events.jsonl.1``
  past ``core_patches.events_max_bytes``.
* ``notified.json``: fingerprints already announced to the home channel (24 h), so a restart loop
  does not repeat a notice.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from hermes_cli import core_patches_policy as policy
from hermes_cli.core_patches_ast import DEFAULT_SOURCE_ROOT, CorePatchFileError, read_lock

logger = logging.getLogger("hermes_cli.core_patches")

NOTICE_TTL_S = 24 * 3600.0
NOTICE_MIN_INTERVAL_S = 30.0
NOTIFY_WAIT_S = 180.0
NOTIFY_POLL_S = 2.0
PROBLEM_STATUSES = ("skipped-drift", "skipped-error", "disabled-after-errors")
_STARTED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

_IO = threading.RLock()  # re-entrant: event() runs under _write_files, which holds it
_LOGGED: set[tuple[str, str, str]] = set()
_ALERTED: set[tuple[str, str]] = set()
_LOCKS: dict[tuple[str, int], dict] = {}
_STATE: dict[str, Any] = {"surface": "", "digest": "", "identity": None, "last_notice": float("-inf"),
                          "max_bytes": policy.Settings().events_max_bytes}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reset_for_tests() -> None:
    _LOGGED.clear()
    _ALERTED.clear()
    _LOCKS.clear()
    _STATE.update(surface="", digest="", identity=None, last_notice=float("-inf"),
                  max_bytes=policy.Settings().events_max_bytes)


def configure(settings: policy.Settings) -> None:
    """Remember the settings the call path needs (``event`` runs on the thread of a patched call and must
    not read the config: the patched function may be the config loader itself)."""
    _STATE["max_bytes"] = settings.events_max_bytes


# ── what the running code is ────────────────────────────────────────────────────────────────


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _upstream_ref(root: Path) -> dict:
    """``{commit, release, date}`` of the upstream the code descends from: the ``HERMES_UPSTREAM_COMMIT`` /
    ``HERMES_UPSTREAM_RELEASE`` environment (Nix installs), else ``scripts/fork/upstream-ref.sh --json`` in a git
    checkout, else the stamp files ``hermes-upstream-commit`` / ``hermes-upstream-release`` at the source root."""
    unknown = {"commit": "unknown", "release": _read_text(root / "hermes-upstream-release") or "unknown",
               "date": "unknown"}
    env_commit = os.environ.get("HERMES_UPSTREAM_COMMIT", "").strip()
    if env_commit:  # a Nix store install has no git history and no repo files: the gateway unit exports these
        release = os.environ.get("HERMES_UPSTREAM_RELEASE", "").strip() or unknown["release"]
        return {"commit": env_commit, "release": release, "date": "unknown"}
    script = root / "scripts" / "fork" / "upstream-ref.sh"
    try:
        if (root / ".git").exists() and script.is_file():
            out = subprocess.run(["bash", str(script), "--json", str(root)], capture_output=True, text=True,
                                 timeout=3, check=False).stdout
            data = json.loads(out)
            return {k: str(data.get(k) or "unknown") for k in ("commit", "release", "date")}
    except Exception:  # health: allow BLE001 -- fall back to the stamp files
        logger.debug("core patches: upstream-ref.sh failed", exc_info=True)
    commit = _read_text(root / "hermes-upstream-commit")
    return {**unknown, "commit": commit or "unknown"}


def _fork_head() -> str:
    env = os.environ.get("HERMES_FORK_HEAD", "").strip()
    if env:
        return env
    try:
        from hermes_cli.version_info import installed_commit_sha

        return installed_commit_sha() or "unknown"
    except Exception:  # health: allow BLE001
        return "unknown"


def identity(root: Optional[Path] = None) -> dict:
    """``{fork_head, upstream_ref}`` of the running code (cached per process; never raises)."""
    if _STATE["identity"] is None:
        try:
            base = Path(root) if root is not None else DEFAULT_SOURCE_ROOT
            _STATE["identity"] = {"fork_head": _fork_head(), "upstream_ref": _upstream_ref(base)}
        except Exception:  # health: allow BLE001
            _STATE["identity"] = {"fork_head": "unknown",
                                  "upstream_ref": {"commit": "unknown", "release": "unknown", "date": "unknown"}}
    return _STATE["identity"]


# ── rows ────────────────────────────────────────────────────────────────────────────────────


def _lock_patches(plugin_dir: Optional[str]) -> dict:
    if not plugin_dir:
        return {}
    try:
        path = Path(plugin_dir) / "core_patches.lock.json"
        cache_key = (str(path), path.stat().st_mtime_ns if path.is_file() else -1)
        if cache_key not in _LOCKS:
            _LOCKS[cache_key] = read_lock(Path(plugin_dir))
        return _LOCKS[cache_key]
    except (CorePatchFileError, OSError, ValueError):
        return {}
    except Exception:  # health: allow BLE001
        return {}


def lock_entry(record: Any) -> dict:
    """The provenance record of a patch from its plugin's lock ({} when there is none)."""
    entry = _lock_patches(getattr(record.spec, "plugin_dir", None)).get(record.spec.name)
    return entry if isinstance(entry, dict) else {}


def row_of(record: Any, failures: Optional[Mapping[str, tuple]] = None) -> dict:
    """The JSON-able description of one patch (status.json, ``--json``, reports)."""
    entry = lock_entry(record)
    upstream = entry.get("upstream_ref") if isinstance(entry.get("upstream_ref"), dict) else {}
    stats = (failures or {}).get(record.spec.key, (0, 0, ""))
    return {
        "plugin": record.spec.plugin_id, "name": record.spec.name, "target": record.spec.target,
        "kind": record.spec.kind, "order": record.spec.order, "critical": record.critical,
        "status": record.status, "detail": record.detail,
        "expect_ast_sha": record.spec.expect_ast_sha, "current_sha": record.current_sha,
        "reason": str(entry.get("reason") or ""),
        "upstream_commit": str(upstream.get("commit") or "unknown"),
        "upstream_release": str(upstream.get("release") or "unknown"),
        "failures": stats[0], "last_error": stats[2],
    }


def _failures() -> Mapping[str, tuple]:
    try:
        from hermes_cli import core_patches

        return core_patches.failure_stats()
    except Exception:  # health: allow BLE001
        return {}


def _short(value: str, n: int = 12) -> str:
    return value[:n] if value else "-"


def _counts(rows: Iterable[Mapping[str, Any]]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return dict(sorted(counts.items()))


# ── logs ────────────────────────────────────────────────────────────────────────────────────


def _log_states(rows: list[dict], surface: str, summary: bool) -> None:
    for row in rows:
        if row["status"] in ("active", "pending"):
            continue
        marker = (f"{row['plugin']}:{row['name']}", row["status"], row["detail"])
        if marker in _LOGGED:
            continue
        _LOGGED.add(marker)
        logger.warning(
            "core patch %s %s: plugin=%s target=%s recorded-upstream=%s (%s) expected-sha=%s current-sha=%s: %s "
            "(hermes plugins patches --agent-brief %s)",
            marker[0], row["status"], row["plugin"], row["target"], _short(row["upstream_commit"]),
            row["upstream_release"], _short(row["expect_ast_sha"]), _short(row["current_sha"]), row["detail"],
            row["name"])
    if summary and rows:
        counts = ", ".join(f"{status}={n}" for status, n in _counts(rows).items())
        logger.info("core patches (%s): %d patch(es): %s", surface or "?", len(rows), counts)


# ── files ───────────────────────────────────────────────────────────────────────────────────


def status_path(directory: Optional[Path] = None) -> Path:
    return (directory or policy.state_dir()) / "status.json"


def read_status(directory: Optional[Path] = None) -> dict:
    """The last status.json ({} when missing or unreadable)."""
    try:
        data = json.loads(status_path(directory).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # health: allow BLE001
        return {}


def _events_for(previous: Mapping[str, Mapping[str, Any]], rows: list[dict]) -> list[dict]:
    kinds = {"active": "applied", "skipped-drift": "drift-first-seen", "skipped-error": "error",
             "disabled-after-errors": "auto-disabled", "skipped-no-grant": "skipped"}
    events = []
    for row in rows:
        key = f"{row['plugin']}:{row['name']}"
        before = previous.get(key) or {}
        if before.get("status") == row["status"] or row["status"] == "pending":
            continue
        kind = kinds.get(row["status"])
        if kind == "applied" and before.get("status") == "skipped-drift":
            kind = "drift-recovered"
        if kind is None:
            continue
        events.append({"event": kind, "key": key, "plugin": row["plugin"], "target": row["target"],
                       "status": row["status"], "detail": row["detail"], "recorded_sha": row["expect_ast_sha"],
                       "before_sha": before.get("current_sha", ""), "after_sha": row["current_sha"],
                       "upstream_commit": row["upstream_commit"]})
    return events


def event(kind: str, **fields: Any) -> None:
    """Append one line to events.jsonl (rotating it past the size cap). Never raises."""
    try:
        directory = policy.safe_state_dir()
        if directory is None:
            return
        line = json.dumps({"ts": _now(), "pid": os.getpid(), "surface": _STATE["surface"], "event": kind, **fields},
                          sort_keys=True)
        with _IO:
            _append_event(directory, line, int(_STATE["max_bytes"]))
    except Exception:  # health: allow BLE001
        logger.debug("core patches: could not write an event", exc_info=True)


def _append_event(directory: Path, line: str, max_bytes: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "events.jsonl"
    if path.is_file() and path.stat().st_size + len(line) + 1 > max_bytes:
        os.replace(path, directory / "events.jsonl.1")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _write_files(rows: list[dict], surface: str, settings: policy.Settings, force: bool) -> list[dict]:
    """status.json + events.jsonl; returns the new events. Called under ``_IO``."""
    directory = policy.safe_state_dir()
    if directory is None:
        return []
    if not rows and not read_status(directory).get("patches"):
        return []  # nothing patched, nothing recorded: a plain `hermes` start writes no file and runs no git
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()
    if digest == _STATE["digest"] and not force and status_path(directory).is_file():
        return []
    previous = read_status(directory)
    before = previous.get("patches") if isinstance(previous.get("patches"), dict) else {}
    now = _now()
    patches = {}
    for row in rows:
        key = f"{row['plugin']}:{row['name']}"
        last = before.get(key) or {}
        patches[key] = {**row, "last_changed": last.get("last_changed", now) if last.get("status") == row["status"]
                        else now}
    events = _events_for(before, rows)
    who = identity()
    body = {"version": 1, "pid": os.getpid(), "surface": surface, "started_at": _STARTED_AT, "updated_at": now,
            "fork_head": who["fork_head"], "upstream_ref": who["upstream_ref"], "counts": _counts(rows),
            "patches": patches}
    for item in events:
        _append_event(directory, json.dumps({"ts": now, "pid": os.getpid(), "surface": surface, **item},
                                            sort_keys=True), settings.events_max_bytes)
    policy.atomic_write_text(status_path(directory), json.dumps(body, indent=2, sort_keys=True) + "\n")
    _STATE["digest"] = digest
    return events


# ── notices ─────────────────────────────────────────────────────────────────────────────────


def _runner_bound() -> bool:
    try:
        from gateway import busy_report

        return busy_report.current_runner() is not None
    except Exception:  # health: allow BLE001
        return False


def _claim_many(directory: Path, fingerprints: list[str]) -> list[str]:
    """The fingerprints not announced in the last 24 h (remembers them). Raises on an unwritable file."""
    path = directory / "notified.json"
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
        seen = seen if isinstance(seen, dict) else {}
    except Exception:  # health: allow BLE001
        seen = {}
    now = time.time()
    seen = {k: v for k, v in seen.items() if isinstance(v, (int, float)) and now - v < NOTICE_TTL_S}
    fresh = [f for f in fingerprints if f not in seen]
    if fresh:
        seen.update({f: now for f in fresh})
        policy.atomic_write_text(path, json.dumps(seen, sort_keys=True) + "\n")
    return fresh


def _send_when_possible(text: str) -> None:
    """Daemon thread: wait (bounded) for the gateway runner, then send ``text`` to the home channel(s)."""
    try:
        from gateway import busy_report, graceful_restart
    except Exception:  # health: allow BLE001 -- no gateway in this process
        return
    deadline = time.monotonic() + NOTIFY_WAIT_S
    while time.monotonic() < deadline:
        try:
            if busy_report.current_runner() is not None and graceful_restart.notify_home(text, timeout=10.0):
                return
        except Exception:  # health: allow BLE001
            logger.debug("core patches: notice failed", exc_info=True)
            return
        time.sleep(NOTIFY_POLL_S)


def _fingerprint(row: Mapping[str, Any]) -> str:
    return f"{row['plugin']}:{row['name']}|{row['status']}|{row['current_sha']}"


def _exception_type(row: Mapping[str, Any]) -> str:
    """The exception type name of the patch's last failure ("" when none or not a plain name)."""
    head = str(row.get("last_error") or "").split(": ", 1)[0]
    return head if re.fullmatch(r"[A-Za-z_][\w.]*", head) else ""


def notice_text(rows: list[Mapping[str, Any]], recovered: list[Mapping[str, Any]]) -> str:
    """Key, status and the exception type name only: never a detail, a path or an exception message."""
    lines = []
    for row in rows:
        kind = _exception_type(row)
        lines.append(f"- {row['plugin']}:{row['name']}: {row['status']}" + (f" ({kind})" if kind else ""))
    lines += [f"- {e['key']}: back to normal (drift recovered)" for e in recovered]
    return "Core patches need attention:\n" + "\n".join(lines)


def notify(rows: list[dict], events: list[dict], settings: policy.Settings, surface: str) -> None:
    """One notice to the home channel for the patches in a problem state that were not announced in the last
    24 h (all in one message). Gateway only (or a process with a bound runner). Never raises."""
    try:
        directory = policy.safe_state_dir()
        problems = [row for row in rows if row["status"] in PROBLEM_STATUSES]
        recovered = [e for e in events if e["event"] == "drift-recovered"]
        if not (problems or recovered) or not settings.notify or directory is None:
            return
        if surface != "gateway" and not _runner_bound():
            return
        if time.monotonic() - _STATE["last_notice"] < NOTICE_MIN_INTERVAL_S:
            return  # not claimed: the next publish announces it
        fresh = set(_claim_many(directory, [_fingerprint(row) for row in problems]
                                + [f"{e['key']}|recovered|{e['after_sha']}" for e in recovered]))
        problems = [row for row in problems if _fingerprint(row) in fresh]
        recovered = [e for e in recovered if f"{e['key']}|recovered|{e['after_sha']}" in fresh]
        if not (problems or recovered):
            return
        _STATE["last_notice"] = time.monotonic()
        threading.Thread(target=_send_when_possible, args=(notice_text(problems, recovered),), daemon=True,
                         name="core-patches-notify").start()
    except Exception:  # health: allow BLE001
        logger.debug("core patches: could not queue a notice", exc_info=True)


# ── entry points called by the registry ─────────────────────────────────────────────────────


def _publish(current: Iterable[Any], surface: str, settings: policy.Settings, summary: bool) -> None:
    failures = _failures()
    rows = [row_of(record, failures) for record in current]
    surface = surface or _STATE["surface"]
    _STATE["surface"] = surface
    _log_states(rows, surface, summary)
    try:
        with _IO:
            events = _write_files(rows, surface, settings, force=summary)
    except Exception:  # health: allow BLE001 -- the files are best effort
        logger.debug("core patches: could not write status.json", exc_info=True)
        return
    notify(rows, events, settings, surface)


def publish(current: Iterable[Any], *, surface: str = "", settings: Optional[policy.Settings] = None,
            summary: bool = False, wait_s: Optional[float] = None) -> None:
    """Log the states, write status.json and events.jsonl, queue a notice. Never raises. With ``wait_s`` the
    work runs in a daemon thread and the caller waits at most that long (the startup path)."""
    try:
        records = tuple(current)
        chosen = settings or policy.Settings()
        if wait_s is None:
            _publish(records, surface, chosen, summary)
            return
        worker = threading.Thread(target=_publish, args=(records, surface, chosen, summary), daemon=True,
                                  name="core-patches-report")
        worker.start()
        worker.join(wait_s)
    except Exception:  # health: allow BLE001
        logger.debug("core patches: publish failed", exc_info=True)


def critical_alert(problems: list[str], notice_lines: list[str]) -> None:
    """A critical patch is not installed and the process starts without it (refusal is off): say so loudly.
    ``problems`` (with details) go to the log and the events; ``notice_lines`` (key, status, type name only)
    are the only text sent to the home channel."""
    try:
        for line in problems:
            if ("critical", line) in _ALERTED:
                continue
            _ALERTED.add(("critical", line))
            logger.error("core patches: critical patch %s; this process runs WITHOUT it (core_patches."
                         "refuse_on_critical is false, so it starts anyway)", line)
            event("critical-skipped", detail=line)
        if problems or notice_lines:
            _notify_critical(notice_lines or ["a critical core patch is not installed"])
    except Exception:  # health: allow BLE001
        logger.debug("core patches: critical alert failed", exc_info=True)


def _notify_critical(notice_lines: list[str]) -> None:
    settings = policy.load_settings()
    directory = policy.safe_state_dir()
    if not settings.notify or directory is None or (_STATE["surface"] != "gateway" and not _runner_bound()):
        return
    if not _claim_many(directory, ["critical|" + "|".join(sorted(notice_lines))]):
        return
    text = "Core patches: a CRITICAL patch is not installed; Hermes started without it.\n" + "\n".join(
        f"- {line}" for line in notice_lines)
    threading.Thread(target=_send_when_possible, args=(text,), daemon=True, name="core-patches-notify").start()


def after_startup_alert(problems: list[str]) -> None:
    """After startup (a reload, an auto-disable): the process keeps running without a critical patch."""
    try:
        for line in problems:
            if ("after-startup", line) in _ALERTED:
                continue
            _ALERTED.add(("after-startup", line))
            logger.error("core patches: critical patch %s after startup; this process runs WITHOUT it until "
                         "it is restarted", line)
            event("critical-skipped", detail=f"after startup: {line}")
    except Exception:  # health: allow BLE001
        logger.debug("core patches: after-startup alert failed", exc_info=True)
