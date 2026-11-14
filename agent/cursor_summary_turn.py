"""One-shot, tool-less Cursor turn that returns text: the public seam for summaries.

Cursor has no chat-completions route (the aux client's compaction call 404s), so anything that needs a
plain completion from a Cursor model has to ride the SDK worker. This is that path, kept apart from the
agent turn (``agent/cursor_runtime.py``): no tools, no transcript, no usage booking, no spinner. A
context-engine plugin calls it; plugins must not import the underscore-private worker names.
"""

from __future__ import annotations

import queue
import tempfile
import time
import uuid
from typing import Any, Optional

from agent import cursor_runtime as _cr
from agent.cursor_runtime import CursorRuntimeError

__all__ = ["run_summary_turn"]


def run_summary_turn(prompt: str, *, model: str, timeout: Optional[float] = None) -> str:
    """Run ``prompt`` on ``model`` through the worker with no tools; return the reply text.

    Raises ``CursorRuntimeError`` (fixed-vocabulary text, never SDK output) on any failure: no key or
    worker, a worker failure code, an unfinished status, a dead worker, an empty reply or the deadline.
    No effort is sent: the worker pins fast off and leaves the model's own default effort."""
    api_key = _cr._require_api_key()
    command = _cr._worker_command()
    deadline = time.monotonic() + (timeout if timeout is not None else _cr._turn_timeout())
    with tempfile.TemporaryDirectory(prefix="hermes-cursor-summary-") as scratch:
        try:
            worker = _cr._Worker(command, _cr._worker_env(api_key, scratch), scratch)
        except OSError as exc:
            raise CursorRuntimeError("Cursor worker could not start") from exc
        with worker:
            turn_id = uuid.uuid4().hex
            start = {
                "type": "start",
                "turnId": turn_id,
                "model": model,
                "prompt": prompt,
                "tools": [],
            }
            if not worker.send(start):
                raise CursorRuntimeError("worker did not accept the turn")
            return _collect(worker, turn_id, deadline)


def _collect(worker: _cr._Worker, turn_id: str, deadline: float) -> str:
    parts: list[str] = []
    while True:
        if time.monotonic() > deadline:
            worker.send({"type": "cancel", "turnId": turn_id})
            raise CursorRuntimeError("summary turn deadline exceeded")
        try:
            message = worker.inbox.get(timeout=_cr._POLL_SECONDS)
        except queue.Empty:
            continue
        if message is None:
            raise CursorRuntimeError("worker exited before finishing the turn")
        if message.get("turnId") not in (turn_id, None):
            continue
        text = _finished_text(message, parts)
        if text is not None:
            return text


def _fold_event(message: dict[str, Any], parts: list[str]) -> None:
    text = message.get("text")
    if message.get("kind") == "text" and isinstance(text, str):
        parts.append(text)


def _reject_tool(message: dict[str, Any], parts: list[str]) -> None:
    raise CursorRuntimeError(
        "The Cursor model attempted a tool in a tool-less summary turn."
    )


def _reject_failed(message: dict[str, Any], parts: list[str]) -> None:
    raise CursorRuntimeError(_cr._failure_text(message.get("code")))


def _finished_reply(message: dict[str, Any], parts: list[str]) -> str:
    if message.get("status") != "finished":
        raise CursorRuntimeError(f"summary turn ended {message.get('status')}")
    reply = "".join(parts).strip()
    if not reply:
        raise CursorRuntimeError("summary turn returned no text")
    return reply


_FINISHED_HANDLERS = {
    "event": _fold_event,
    "tool-request": _reject_tool,
    "failed": _reject_failed,
    "done": _finished_reply,
}


def _finished_text(message: dict[str, Any], parts: list[str]) -> Optional[str]:
    """Fold one worker line into ``parts``; the reply text once the turn is finished, else None."""
    handler = _FINISHED_HANDLERS.get(message.get("type"))
    if handler is not None:
        return handler(message, parts)
    return None
