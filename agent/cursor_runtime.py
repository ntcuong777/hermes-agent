"""Cursor SDK runtime: hand the whole turn to a Node worker that owns the model loop.

Hermes keeps everything else. The worker (hermes/cursor-runtime/worker-main.ts) speaks a line protocol
over stdio; its pipe is the tool bridge. Each model tool call arrives as a ``tool-request`` and runs
through ``agent._execute_tool_calls`` (guardrails, approvals, middleware, transforms, persistence), so
the worker never touches a Hermes registry tool and has no native tools (MCP-only SDK options).
No fallback: any failure ends the turn with an error.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from agent.codex_runtime import (
    _call_guarded,
    _codex_developer_instructions,
    _consume_user_interrupt,
    _turn_result,
)
from agent.codex_runtime_history_seed import _text_of, render_history_seed
from agent.message_metadata import append_message
from agent.turn_tool_round import stage_tool_call_message

logger = logging.getLogger(__name__)

CURSOR_SDK_API_MODE = "cursor_sdk"
MAX_WORKER_LINE_CHARS = 1024 * 1024
_POLL_SECONDS = 0.2
_CANCEL_GRACE_SECONDS = 5.0
_DEFAULT_TURN_TIMEOUT_SECONDS = 1800.0
_WORKER_ENV_ALLOWLIST = (
    "PATH",
    "LANG",
    "TMPDIR",
    "SSL_CERT_FILE",
    "NIX_SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


class CursorRuntimeError(RuntimeError):
    """A configuration or worker failure; the message never carries credentials or SDK text."""


def run_cursor_sdk_turn(
    agent,
    *,
    user_message: Any,
    original_user_message: Any,
    messages: list[dict[str, Any]],
    effective_task_id: str,
    should_review_memory: bool = False,
) -> dict[str, Any]:
    """Run one Hermes turn on the Cursor SDK worker; returns the chat_completions result shape.
    The user message is ALREADY in ``messages`` — never append it again."""
    try:
        api_key = _require_api_key()
        command = _worker_command()
    except CursorRuntimeError as exc:
        return _failed_result(agent, messages, str(exc))
    with tempfile.TemporaryDirectory(prefix="hermes-cursor-") as scratch:
        try:
            worker = _Worker(command, _worker_env(api_key, scratch), scratch)
        except OSError:
            return _failed_result(agent, messages, "Cursor worker could not start")
        with worker:
            turn = _Turn(agent, worker, messages, effective_task_id)
            outcome = turn.run(
                _start_message(agent, user_message, messages, advertised_tools(agent))
            )
    return _finish(
        agent, turn, outcome, messages, original_user_message, should_review_memory
    )


def advertised_tools(agent) -> list[dict[str, Any]]:
    """Wire descriptors for exactly the tools the live agent has enabled this turn (its active schemas)."""
    valid = getattr(agent, "valid_tool_names", None) or set()
    functions = [
        t["function"]
        for t in getattr(agent, "tools", None) or []
        if t.get("type") == "function"
    ]
    return [
        {
            "name": f["name"],
            "description": f.get("description") or "",
            "inputSchema": f.get("parameters") or {"type": "object"},
        }
        for f in functions
        if f["name"] in valid
    ]


def _require_api_key() -> str:
    key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not key:
        raise CursorRuntimeError(
            "CURSOR_API_KEY is not set; the Cursor SDK runtime cannot start"
        )
    return key


def _worker_command() -> list[str]:
    from hermes_cli.config import load_config

    cfg = load_config().get("cursor") or {}
    runtime_dir = os.environ.get("HERMES_CURSOR_RUNTIME_DIR") or cfg.get("runtime_dir")
    if not runtime_dir:
        raise CursorRuntimeError(
            "cursor.runtime_dir (or HERMES_CURSOR_RUNTIME_DIR) is not set"
        )
    node = cfg.get("node") or shutil.which("node") or "node"
    return [str(node), str(Path(str(runtime_dir)) / "worker-main.ts")]


def _worker_env(api_key: str, scratch: str) -> dict[str, str]:
    """Restrictive environment: no inherited secrets, an empty HOME so no ambient Cursor settings load."""
    home = os.path.join(scratch, "home")
    os.makedirs(home, exist_ok=True)
    base = {
        name: os.environ[name] for name in _WORKER_ENV_ALLOWLIST if name in os.environ
    }
    return {
        **base,
        "CURSOR_API_KEY": api_key,
        "HOME": home,
        "XDG_CONFIG_HOME": home,
        "XDG_CACHE_HOME": home,
    }


def _start_message(
    agent,
    user_message: Any,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "start",
        "turnId": uuid.uuid4().hex,
        "model": str(getattr(agent, "model", "") or ""),
        "prompt": _prompt(agent, user_message, messages),
        "tools": tools,
    }


def _prompt(agent, user_message: Any, messages: list[dict[str, Any]]) -> str:
    """System prompt, transformed history as already-happened context, then the current user turn."""
    parts = [
        _codex_developer_instructions(agent),
        render_history_seed(messages),
        f"[USER]\n{_text_of(user_message)}",
    ]
    return "\n\n".join(part for part in parts if part)


class _Worker:
    """One worker process. Reader thread turns stdout lines into queue items; stderr is discarded
    because SDK diagnostics may carry secrets."""

    def __init__(self, command: list[str], env: dict[str, str], cwd: str) -> None:
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
            cwd=cwd,
        )
        self.inbox: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def __enter__(self) -> _Worker:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def send(self, message: dict[str, Any]) -> bool:
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def close(self) -> None:
        with _suppressed(OSError, ValueError):
            self._proc.stdin.close()
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=_CANCEL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

    def _read(self) -> None:
        for line in iter(
            lambda: self._proc.stdout.readline(MAX_WORKER_LINE_CHARS + 1), ""
        ):
            if len(line) > MAX_WORKER_LINE_CHARS:
                self.inbox.put(
                    {"type": "failed", "code": "oversized-message", "turnId": None}
                )
                break
            parsed = _parse_line(line)
            if parsed is not None:
                self.inbox.put(parsed)
        self.inbox.put(None)  # EOF


def _parse_line(line: str) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class _suppressed:
    def __init__(self, *types: type[BaseException]) -> None:
        self._types = types

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, self._types)


class _Outcome:
    """Terminal state of one worker turn. ``error`` is a fixed-vocabulary label, never SDK text."""

    def __init__(self, status: str, error: Optional[str] = None) -> None:
        self.status = status
        self.error = error


class _Turn:
    def __init__(
        self, agent, worker: _Worker, messages: list[dict[str, Any]], task_id: str
    ) -> None:
        self.agent, self.worker, self.messages, self.task_id = (
            agent,
            worker,
            messages,
            task_id,
        )
        self.turn_id = ""
        self.text_parts: list[str] = []
        self.thinking_parts: list[str] = []
        self.tool_calls = 0
        self.final_text = ""
        self.spinner_on = False

    def run(self, start: dict[str, Any]) -> _Outcome:
        self.turn_id = start["turnId"]
        if not self.worker.send(start):
            return _Outcome("error", "worker did not accept the turn")
        deadline = time.monotonic() + _turn_timeout()
        while True:
            if getattr(self.agent, "_interrupt_requested", False):
                return self._cancel()
            if time.monotonic() > deadline:
                self._cancel()
                return _Outcome("error", "turn deadline exceeded")
            outcome = self._step()
            if outcome is not None:
                return outcome

    def _step(self) -> Optional[_Outcome]:
        try:
            message = self.worker.inbox.get(timeout=_POLL_SECONDS)
        except queue.Empty:
            return None
        if message is None:
            return _Outcome("error", "worker exited before finishing the turn")
        if message.get("type") == "ready" or message.get("turnId") not in (
            self.turn_id,
            None,
        ):
            return None
        return self._dispatch(message)

    def _dispatch(self, message: dict[str, Any]) -> Optional[_Outcome]:
        kind = message.get("type")
        if kind == "event":
            self._on_event(message)
            return None
        if kind == "tool-request":
            return self._on_tool_request(message)
        if kind == "done":
            return _Outcome(str(message.get("status")))
        if kind == "failed":
            return _Outcome("error", f"worker failed: {message.get('code')}")
        return None

    def _on_event(self, message: dict[str, Any]) -> None:
        text = message.get("text")
        if not isinstance(text, str) or not text:
            return
        kind = message.get("kind")
        if kind == "progress":
            self._spinner(f"Cursor: {text}")
        elif kind == "thinking":
            # Actual thinking only; progress never reaches the reasoning pane.
            self.thinking_parts.append(text)
            self.agent._fire_reasoning_delta(text)
        elif kind == "text":
            self._spinner("")
            self.text_parts.append(text)
            self.agent._fire_stream_delta(text)

    def _spinner(self, label: str) -> None:
        """Honest progress on the spinner rail (distinct from the reasoning renderer). Empty clears it."""
        if not label and not self.spinner_on:
            return
        self.spinner_on = bool(label)
        callback = getattr(self.agent, "thinking_callback", None)
        if callback:
            with _suppressed(Exception):
                callback(label)

    def _on_tool_request(self, message: dict[str, Any]) -> Optional[_Outcome]:
        name, args, call_id = (
            message.get("name"),
            message.get("args"),
            message.get("callId"),
        )
        if name not in (getattr(self.agent, "valid_tool_names", None) or set()):
            return self._reply(call_id, "Tool is not available in this runtime.", True)
        if not isinstance(args, dict):
            return self._reply(call_id, "Tool arguments must be an object.", True)
        return self._run_tool(
            f"cursor_{self.turn_id[:8]}_{call_id}", name, args, call_id
        )

    def _run_tool(
        self, hermes_call_id: str, name: str, args: dict[str, Any], call_id: Any
    ) -> Optional[_Outcome]:
        assistant_message = SimpleNamespace(
            content="".join(self.text_parts),
            reasoning="".join(self.thinking_parts) or None,
            tool_calls=[
                SimpleNamespace(
                    id=hermes_call_id,
                    type="function",
                    function=SimpleNamespace(name=name, arguments=json.dumps(args)),
                )
            ],
        )
        self.text_parts, self.thinking_parts = [], []
        assistant_row, _ = stage_tool_call_message(
            self.agent,
            assistant_message=assistant_message,
            finish_reason="tool_calls",
            messages=self.messages,
        )
        append_message(self.messages, assistant_row)
        # The tool-call row is durable before any side effect so resume sees what ran.
        if self.agent._flush_messages_to_session_db(self.messages) is False:
            self._cancel()
            return _Outcome("error", "transcript write failed before tool execution")
        stream_end = getattr(self.agent, "stream_delta_callback", None)
        if stream_end:
            with _suppressed(Exception):
                stream_end(None)
        self._spinner("")
        self.tool_calls += 1
        self.agent._execute_tool_calls(
            assistant_message, self.messages, self.task_id, self.tool_calls
        )
        if getattr(self.agent, "_incremental_persistence_failed", False):
            self._cancel()
            return _Outcome("error", "transcript write failed after tool execution")
        text, is_error = _tool_result(self.messages, hermes_call_id)
        return self._reply(call_id, text, is_error)

    def _reply(self, call_id: Any, text: str, is_error: bool) -> Optional[_Outcome]:
        sent = self.worker.send(
            {
                "type": "tool-result",
                "turnId": self.turn_id,
                "callId": call_id,
                "text": text,
                "isError": is_error,
            }
        )
        return (
            None
            if sent
            else _Outcome("error", "worker closed before the tool result was delivered")
        )

    def _cancel(self) -> _Outcome:
        """Ask the worker to stop and give it a bounded moment to acknowledge; close() force-kills."""
        self.worker.send({"type": "cancel", "turnId": self.turn_id})
        deadline = time.monotonic() + _CANCEL_GRACE_SECONDS
        while time.monotonic() < deadline:
            try:
                message = self.worker.inbox.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                continue
            if message is None or message.get("type") in ("done", "failed"):
                break
        return _Outcome("cancelled")


def _tool_result(messages: list[dict[str, Any]], call_id: str) -> tuple[str, bool]:
    row = next(
        (
            m
            for m in reversed(messages)
            if m.get("role") == "tool" and m.get("tool_call_id") == call_id
        ),
        None,
    )
    if row is None:
        return "Tool produced no result.", True
    text = _result_text(row.get("content"))
    return text, _looks_failed(row.get("name"), text)


def _result_text(content: Any) -> str:
    """Model-facing text of a tool result; multimodal envelopes keep their text parts only."""
    if isinstance(content, str):
        return content
    try:
        from agent.tool_executor import (
            _is_multimodal_tool_result,
            _multimodal_text_summary,
        )

        if _is_multimodal_tool_result(content):
            return str(_multimodal_text_summary(content))
    except Exception:
        logger.debug("multimodal tool result summary failed", exc_info=True)
    return json.dumps(content, default=str)


def _looks_failed(name: Any, text: str) -> bool:
    try:
        from agent.tool_executor import _detect_tool_failure

        return bool(_detect_tool_failure(str(name or ""), text)[0])
    except Exception:
        return False


def _turn_timeout() -> float:
    try:
        from hermes_cli.config import load_config

        return float(
            (load_config().get("cursor") or {}).get("turn_timeout")
            or _DEFAULT_TURN_TIMEOUT_SECONDS
        )
    except Exception:
        return _DEFAULT_TURN_TIMEOUT_SECONDS


def _failed_result(agent, messages: list[dict[str, Any]], error: str) -> dict[str, Any]:
    return _turn_result(
        _consume_user_interrupt(agent),
        messages,
        api_calls=0,
        completed=False,
        error=error,
        final_response=f"Cursor runtime failed: {error}",
        agent_persisted=True,
    )


def _finish(
    agent,
    turn: _Turn,
    outcome: _Outcome,
    messages: list[dict[str, Any]],
    original_user_message: Any,
    should_review_memory: bool,
) -> dict[str, Any]:
    turn._spinner("")
    interrupt = _consume_user_interrupt(agent, outcome.status == "cancelled")
    completed = outcome.status == "finished"
    final_text = "".join(turn.text_parts) if completed else ""
    if final_text:
        row = agent._build_assistant_message(
            SimpleNamespace(
                content=final_text,
                tool_calls=None,
                reasoning="".join(turn.thinking_parts) or None,
            ),
            "stop",
        )
        append_message(messages, row)
    agent._flush_messages_to_session_db(messages)
    agent._iters_since_skill = getattr(agent, "_iters_since_skill", 0) + turn.tool_calls
    if completed:
        _call_guarded(
            getattr(agent, "_sync_external_memory_for_turn", None),
            "external memory sync raised",
            kwargs=dict(
                original_user_message=original_user_message,
                final_response=final_text,
                interrupted=False,
                messages=messages,
            ),
        )
    error = (
        None
        if completed or interrupt[0]
        else (outcome.error or f"turn ended with status {outcome.status}")
    )
    return _turn_result(
        interrupt,
        messages,
        api_calls=1,
        completed=completed,
        error=error,
        final_response=final_text
        or (f"Cursor runtime failed: {error}" if error else ""),
        agent_persisted=True,
    )
