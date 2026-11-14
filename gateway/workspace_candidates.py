"""Candidate workspaces for ``/cwd``: the directories this profile actually works in.

Projects and discovered repos come from ``projects.db`` (``hermes projects``), session-derived
roots from the session store's ``cwd`` column — the same merge the dashboard Chat tab's picker
shows (``chat_workspaces._collect_workspaces``). The tui_gateway helpers only live on
``tui_gateway.server``'s namespace (``tui_gateway/method_ctx.py`` rebinds them against its
globals), so the import happens here, lazily, inside the body — the messaging gateway already
lazy-imports tui_gateway submodules and module import is loop-safe
(``tests/gateway/test_import_preserves_tui_cwd.py`` guards it).

Fail-soft throughout: a candidates failure must never fail the ``/cwd`` command or a Discord
autocomplete keystroke — whatever was collected so far is returned, an empty list at worst.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Discord caps autocomplete at 25 choices; the reply list stays equally bounded.
CANDIDATE_CAP = 25


def _isdir(path: str) -> bool:
    return bool(path) and os.path.isdir(os.path.abspath(os.path.expanduser(path)))


def _candidate_paths(session_db) -> list[str]:
    """(projects' paths, repo payload paths) before dedup/cap; failures degrade to empty."""
    projects: list[str] = []
    repos: list[str] = []
    try:
        from hermes_cli import projects_db as pdb
        with pdb.connect_closing() as conn:
            for project in pdb.list_projects(conn):
                info = project.to_dict()
                folders = info.get("folders") or []
                paths = [str(p.get("path") or "") for p in folders]
                primary = str(info.get("primary_path") or "")
                if primary in paths:
                    paths.remove(primary)
                if primary:
                    paths.insert(0, primary)
                projects.extend(p for p in paths if p)
    except Exception:  # health: allow BLE001 -- candidates are advisory; projects.db problems must not fail the command
        logger.debug("workspace candidates: projects.db unavailable", exc_info=True)
    try:
        import tui_gateway.server as gateway
        # Cache the discovery policy: with scanning disabled the dashboard hides the cached repos
        # too, and this list must agree with what the picker shows.
        payload = gateway._discover_repos_payload(
            session_db, backfill=False, include_cached=bool(gateway._repo_discovery_policy()["enabled"]))
        repos = [str(r.get("root") or "") for r in payload if r.get("root")]
    except Exception:  # health: allow BLE001 -- _discover_repos_payload does real db/git I/O; its failures must not fail the command
        logger.debug("workspace candidates: repo discovery unavailable", exc_info=True)
    return projects, repos


def candidate_workspaces(session_db=None) -> list[str]:
    """The profile's workspace directories, deduped, most-relevant first, capped at
    :data:`CANDIDATE_CAP`. ``session_db`` (a SessionDB with ``distinct_session_cwds``) adds
    session-derived roots; ``None`` keeps projects and the discovered-repo cache only."""
    projects, repos = _candidate_paths(session_db)

    seen: set[str] = set()
    ordered: list[str] = []
    for raw in projects + repos:
        if not _isdir(raw):
            continue
        path = os.path.abspath(os.path.expanduser(raw))
        key = os.path.normcase(os.path.realpath(path))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
        if len(ordered) >= CANDIDATE_CAP:
            break
    return ordered


def match_workspace_prefix(candidates: list[str], prefix: str) -> tuple[list[str], str]:
    """``(matches, note)`` for a typed ``/cwd <prefix>``: case-insensitive match on the path and
    its basename. ``note`` is ``""`` (no match — fall through to literal-path handling),
    ``"unique"`` or ``"ambiguous"``."""
    text = (prefix or "").strip()
    if not text:
        return [], ""
    lowered = text.lower()
    matches = [
        path for path in candidates
        if lowered in path.lower()
        or lowered in os.path.basename(path.rstrip(os.sep)).lower()
    ]
    if not matches:
        return [], ""
    return matches, ("unique" if len(matches) == 1 else "ambiguous")
