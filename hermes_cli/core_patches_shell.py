"""Call-time safety shell of the core-patch registry (fork-owned, stdlib only).

Every installed patch (wrap or replace) is wrapped in a shell that keeps a faulty patch from breaking
the call. The shell guards ONLY the patch layer; whatever the original raises still propagates.

What counts as a patch-layer error (counted, logged, rate limited, answered by the original):

* ``kind="wrap"``: an ``Exception`` raised by the wrapper's own code (its logic before it calls
  ``original`` or after ``original`` returned). The trampoline handed to the wrap factory records the
  outcome of the original call in a per-call frame, so the shell knows which side raised:

  - the wrapper failed BEFORE it called ``original``: the call is answered by the next inner callable
    (the original, or the next patch in the chain), run fresh;
  - the wrapper failed AFTER ``original`` returned: the original already ran (side effects), so it is
    NOT run again: the shell returns the value ``original`` returned;
  - ``original`` raised (the wrapper let it through, or raised something else after it): legitimate,
    propagated, not counted. The original is never run twice.

* ``kind="replace"``: an ``Exception`` raised by the replacement body. What happens next depends on
  ``Guard.replace_fallback`` (``core_patches.replace_fallback``):

  - ``"bug-class"`` (default): a ``BUG_CLASS`` exception (TypeError, AttributeError, NameError,
    ImportError, AssertionError, NotImplementedError) is a patch fault: the recorded original answers
    the call. A drifted patch (``Guard.drifted``, spec 09) is answered for ANY ``Exception``. Any other exception looks legitimate: it propagates, not counted, and the original is
    NOT run (the replacement may already have had side effects).
  - ``"always"``: every exception is answered by the recorded original, as the rule below describes.
  - ``"never"``: every exception propagates, counted, and the original is NOT run.

  When the original answers: if it raises the same exception type, the exception was legitimate (the
  original agrees): it propagates and is not counted. If the original answers, or raises a different
  type, the replacement is at fault and the failure is counted.

Never patch-layer errors, always propagated untouched and uncounted: every ``BaseException`` that is
not an ``Exception`` (KeyboardInterrupt, SystemExit, GeneratorExit, asyncio.CancelledError), and
the exception types a patch declares in ``propagates=`` (an exception it raises on purpose).

Generators: a wrapper or replacement of a generator (or async generator) target is guarded step by
step. A wrapper that fails before it started the original stream is answered by the original stream;
once the wrapper has started the original stream (even before the first item) a failure propagates
and is NOT counted (it cannot be told apart from the stream's own error), because the stream cannot be
restarted. A failure of the wrapper after the first item was handed out propagates (counted), so a
patch that breaks mid-stream is switched off for the next calls.

Untracked entries: a trampoline entry is attributed to a call only through the frame of THAT patch
(frames are matched per patch, ``_frame_for``). An original entered from a context the shell does not
see (another thread, a pool, a context copied away) is an untracked entry: the shell counts it on the
guard. When a wrapper fails with no tracked entry but with untracked entries, the original may already
have run, so it is NOT run again: the failure is counted and propagates.

``max_failures`` consecutive patch-layer failures switch the patch off for the process: the shell
passes every later call straight to the original and tells the registry (status
``disabled-after-errors``).
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("hermes_cli.core_patches")

LOG_INTERVAL_S = 60.0
BUG_CLASS = (TypeError, AttributeError, NameError, ImportError, AssertionError, NotImplementedError)
SYNC, ASYNC, GEN, AGEN = "sync", "async", "gen", "agen"
IDLE, RUNNING, RETURNED, RAISED = "idle", "running", "returned", "raised"
RAISE, FALLBACK, CHECKED, RETURN = "raise", "fallback", "checked", "return"

_FRAME: "contextvars.ContextVar[Optional[Frame]]" = contextvars.ContextVar("core_patch_frame", default=None)


def flavor_of(func: Callable[..., Any]) -> str:
    """How ``func`` is called: ``async`` (coroutine function), ``gen``, ``agen`` or ``sync``."""
    if inspect.isasyncgenfunction(func):
        return AGEN
    if inspect.iscoroutinefunction(func):
        return ASYNC
    return GEN if inspect.isgeneratorfunction(func) else SYNC


class Frame:
    """What one shell call saw of the original: how many times it was entered and how the last entry ended."""

    __slots__ = ("entries", "state", "result", "error", "guard", "parent", "closed")

    def __init__(self, guard: Optional["Guard"] = None, parent: Optional["Frame"] = None) -> None:
        self.entries, self.state, self.result, self.error = 0, IDLE, None, None
        self.guard, self.parent = guard, parent
        self.closed = False  # set once the shell call that owns this frame has finished (see _frame_for)

    def enter(self) -> "Frame":
        self.entries, self.state = self.entries + 1, RUNNING
        return self

    def returned(self, result: Any) -> None:
        self.state, self.result = RETURNED, result

    def raised(self, error: BaseException) -> None:
        self.state, self.error = RAISED, error


class Guard:
    """Failure accounting of one patch; shared by its shell and the registry (which owns ``on_failure``)."""

    def __init__(self, key: str, kind: str, max_failures: int, propagates: tuple = (),
                 on_failure: Optional[Callable[["Guard", BaseException, str, bool], None]] = None,
                 replace_fallback: str = "bug-class") -> None:
        self.key, self.kind, self.max_failures, self.propagates = key, kind, max(1, int(max_failures)), tuple(propagates)
        self.on_failure = on_failure
        self.replace_fallback = replace_fallback if replace_fallback in ("always", "never") else "bug-class"
        self.drifted = False  # set by core_patches._commit: a drifted replace answers every Exception (spec 09)
        self.consecutive = 0
        self.total = 0
        self.untracked_entries = 0
        self.disabled = False
        self.last_error = ""
        self._last_log = float("-inf")
        self._lock = threading.Lock()

    def declared(self, exc: BaseException) -> bool:
        return bool(self.propagates) and isinstance(exc, self.propagates)

    def note_untracked(self) -> None:
        """The original was entered outside any frame of this patch (see the module docstring)."""
        with self._lock:
            self.untracked_entries += 1

    def ok(self) -> None:
        if self.consecutive:
            self.consecutive = 0

    def fail(self, exc: BaseException, where: str) -> bool:
        """Count one patch-layer failure; True when it switched the patch off. Never raises."""
        try:
            with self._lock:
                self.total += 1
                self.consecutive += 1
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                tripped = self.consecutive >= self.max_failures and not self.disabled
                if tripped:
                    self.disabled = True
                now = time.monotonic()
                loud = tripped or now - self._last_log >= LOG_INTERVAL_S
                if loud:
                    self._last_log = now
            if loud:
                logger.warning("core patch %s raised in %s (%d in a row, %d in total); the original answered the call",
                               self.key, where, self.consecutive, self.total,
                               exc_info=(type(exc), exc, exc.__traceback__))
            if tripped:
                logger.error("core patch %s switched off after %d failures in a row; the original runs from now on "
                             "(last: %s)", self.key, self.consecutive, self.last_error)
            if loud and self.on_failure is not None:
                self.on_failure(self, exc, where, tripped)
            return tripped
        except Exception:  # health: allow BLE001 -- accounting must never break the call it guards
            logger.debug("core patch %s: failure accounting failed", self.key, exc_info=True)
            return False

    def settle(self, caught: BaseException, answered: Optional[BaseException]) -> None:
        """A replacement body raised ``caught`` and the original then answered (``answered`` = its exception)."""
        if answered is not None and type(answered) is type(caught):
            self.ok()  # the original agrees: a legitimate exception, not a patch fault
        else:
            self.fail(caught, "the replacement body")


# ── the trampoline handed to a wrap factory ─────────────────────────────────────────────────


def _track_gen(stream: Any, frame: Frame) -> Any:
    try:
        result = yield from stream
    except BaseException as exc:  # health: allow BLE001 -- recorded, then re-raised untouched
        frame.raised(exc)
        raise
    frame.returned(result)
    return result


async def _track_agen(stream: Any, frame: Frame) -> Any:
    try:
        async for item in stream:
            yield item
    except BaseException as exc:  # health: allow BLE001 -- recorded, then re-raised untouched
        frame.raised(exc)
        raise
    frame.returned(None)


def _frame_for(guard: Guard) -> Optional[Frame]:
    """The innermost frame of the current call that belongs to ``guard`` (None when there is none)."""
    frame = _FRAME.get()
    while frame is not None and frame.guard is not guard:
        frame = frame.parent
    return None if frame is not None and frame.closed else frame


def trampoline(guard: Guard, inner: Callable[[], Callable[..., Any]], original: Callable[..., Any],
               flavor: str) -> Callable[..., Any]:
    """The ``original`` a wrap factory receives: forwards to ``inner()`` and records the outcome in the
    frame of ``guard`` in the current call. An entry with no such frame is untracked: it is counted on
    the guard and forwarded as before."""
    if flavor == ASYNC:
        async def forward(*args: Any, **kwargs: Any) -> Any:
            frame = _frame_for(guard)
            if frame is None:
                guard.note_untracked()
                return await inner()(*args, **kwargs)
            frame.enter()
            try:
                result = await inner()(*args, **kwargs)
            except BaseException as exc:  # health: allow BLE001 -- recorded, then re-raised untouched
                frame.raised(exc)
                raise
            frame.returned(result)
            return result
    elif flavor in (GEN, AGEN):
        def forward(*args: Any, **kwargs: Any) -> Any:
            frame = _frame_for(guard)
            stream = inner()(*args, **kwargs)
            if frame is None:
                guard.note_untracked()
                return stream
            return (_track_gen if flavor == GEN else _track_agen)(stream, frame.enter())
    else:
        def forward(*args: Any, **kwargs: Any) -> Any:
            frame = _frame_for(guard)
            if frame is None:
                guard.note_untracked()
                return inner()(*args, **kwargs)
            frame.enter()
            try:
                result = inner()(*args, **kwargs)
            except BaseException as exc:  # health: allow BLE001 -- recorded, then re-raised untouched
                frame.raised(exc)
                raise
            frame.returned(result)
            return result
    return functools.wraps(original)(forward)


# ── the decision ────────────────────────────────────────────────────────────────────────────


def _decide(guard: Guard, frame: Frame, exc: BaseException, *, delivered: bool, can_return: bool,
            untracked: int = 0) -> str:
    """RAISE (propagate ``exc``), FALLBACK (the inner callable answers; already counted), CHECKED (replace:
    the original answers, then ``Guard.settle`` counts) or RETURN (give back ``frame.result``; counted).
    ``untracked``: untracked entries of the original during this call (see ``Guard.note_untracked``)."""
    if not isinstance(exc, Exception) or guard.declared(exc):
        guard.ok()
        return RAISE
    if guard.kind == "replace":
        if delivered:
            guard.fail(exc, "the replacement body (mid-stream)")
            return RAISE
        if guard.replace_fallback == "never":
            guard.fail(exc, "the replacement body")
            return RAISE
        if guard.replace_fallback == "bug-class" and not guard.drifted and not isinstance(exc, BUG_CLASS):
            guard.ok()  # looks like a legitimate error: the upstream function is not run a second time
            return RAISE
        return CHECKED
    if frame.state == RAISED:
        guard.ok()  # the original raised: legitimate
        return RAISE
    if delivered:
        guard.fail(exc, "the wrapper (mid-stream)")
        return RAISE
    if frame.entries == 0 and untracked == 0:
        guard.fail(exc, "the wrapper before it called the original")
        return FALLBACK
    if frame.entries == 0 and untracked > 0:
        guard.fail(exc, "the wrapper (the original ran outside this call's context)")
        return RAISE  # the original may already have run: never run it again
    if frame.state == RETURNED and can_return:
        guard.fail(exc, "the wrapper after the original returned")
        return RETURN
    return RAISE  # the original is mid-flight or ran more than once: never run it again


def _call_in_frame(frame: Frame, func: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
    token = _FRAME.set(frame)
    try:
        return func(*args, **kwargs)
    finally:
        _FRAME.reset(token)


# ── the shells ──────────────────────────────────────────────────────────────────────────────


def _sync_shell(guard: Guard, wrapped: Callable[..., Any], inner: Callable[[], Callable[..., Any]]) -> Callable[..., Any]:
    def shell(*args: Any, **kwargs: Any) -> Any:
        if guard.disabled:
            return inner()(*args, **kwargs)
        frame = Frame(guard, _FRAME.get())
        before = guard.untracked_entries
        try:
            result = _call_in_frame(frame, wrapped, args, kwargs)
        except BaseException as exc:  # health: allow BLE001 -- classified by _decide; RAISE re-raises it
            frame.closed = True
            untracked = guard.untracked_entries - before
            action = _decide(guard, frame, exc, delivered=False, can_return=True, untracked=untracked)
            if action == RAISE:
                raise
            caught = exc
        else:
            frame.closed = True
            guard.ok()
            return result
        if action == RETURN:
            return frame.result
        if action == CHECKED:
            try:
                value = inner()(*args, **kwargs)
            except Exception as again:
                guard.settle(caught, again)
                raise
            guard.settle(caught, None)
            return value
        return inner()(*args, **kwargs)
    return shell


def _async_shell(guard: Guard, wrapped: Callable[..., Any], inner: Callable[[], Callable[..., Any]]) -> Callable[..., Any]:
    async def shell(*args: Any, **kwargs: Any) -> Any:
        if guard.disabled:
            return await inner()(*args, **kwargs)
        frame = Frame(guard, _FRAME.get())
        before = guard.untracked_entries
        token = _FRAME.set(frame)
        try:
            result = await wrapped(*args, **kwargs)
        except BaseException as exc:  # health: allow BLE001 -- classified by _decide; RAISE re-raises it
            frame.closed = True
            _FRAME.reset(token)
            untracked = guard.untracked_entries - before
            action = _decide(guard, frame, exc, delivered=False, can_return=True, untracked=untracked)
            if action == RAISE:
                raise
            caught = exc
        else:
            frame.closed = True
            _FRAME.reset(token)
            guard.ok()
            return result
        if action == RETURN:
            return frame.result
        if action == CHECKED:
            try:
                value = await inner()(*args, **kwargs)
            except Exception as again:
                guard.settle(caught, again)
                raise
            guard.settle(caught, None)
            return value
        return await inner()(*args, **kwargs)
    return shell


def _step(stream: Any, sent: Any, thrown: Optional[BaseException]) -> Any:
    if thrown is not None:
        throw = getattr(stream, "throw", None)
        if throw is None:
            raise thrown
        return throw(thrown)
    return next(stream) if sent is None else stream.send(sent)


def _gen_shell(guard: Guard, wrapped: Callable[..., Any], inner: Callable[[], Callable[..., Any]]) -> Callable[..., Any]:
    def shell(*args: Any, **kwargs: Any) -> Any:
        if guard.disabled:
            return (yield from inner()(*args, **kwargs))
        frame, delivered, sent, thrown = Frame(guard, _FRAME.get()), False, None, None
        before = guard.untracked_entries
        try:
            stream = iter(_call_in_frame(frame, wrapped, args, kwargs))
            while True:
                token = _FRAME.set(frame)
                try:
                    item = _step(stream, sent, thrown)
                except StopIteration as stop:
                    frame.closed = True
                    guard.ok()
                    return stop.value
                finally:
                    _FRAME.reset(token)
                delivered, thrown = True, None
                try:
                    sent = yield item
                except GeneratorExit:
                    close = getattr(stream, "close", None)
                    if close is not None:
                        close()
                    raise
                except BaseException as exc:  # health: allow BLE001 -- forwarded into the wrapper's stream
                    sent, thrown = None, exc
        except BaseException as exc:  # health: allow BLE001 -- classified by _decide; RAISE re-raises it
            frame.closed = True
            if exc is thrown:
                raise  # the consumer threw it in and the stream let it out: not the patch's fault
            untracked = guard.untracked_entries - before
            action = _decide(guard, frame, exc, delivered=delivered, can_return=False, untracked=untracked)
            if action == RAISE:
                raise
            caught = exc
        if action == CHECKED:
            try:
                value = yield from inner()(*args, **kwargs)
            except Exception as again:
                guard.settle(caught, again)
                raise
            guard.settle(caught, None)
            return value
        return (yield from inner()(*args, **kwargs))
    return shell


async def _astep(stream: Any, sent: Any, thrown: Optional[BaseException]) -> Any:
    if thrown is not None:
        athrow = getattr(stream, "athrow", None)
        if athrow is None:
            raise thrown
        return await athrow(thrown)
    return await (stream.__anext__() if sent is None else stream.asend(sent))


def _agen_shell(guard: Guard, wrapped: Callable[..., Any], inner: Callable[[], Callable[..., Any]]) -> Callable[..., Any]:
    async def shell(*args: Any, **kwargs: Any) -> Any:
        if guard.disabled:
            async for item in inner()(*args, **kwargs):
                yield item
            return
        frame, delivered, sent, thrown = Frame(guard, _FRAME.get()), False, None, None
        before = guard.untracked_entries
        try:
            stream = _call_in_frame(frame, wrapped, args, kwargs)
            stream = stream if hasattr(stream, "__anext__") else stream.__aiter__()
            while True:
                token = _FRAME.set(frame)
                try:
                    item = await _astep(stream, sent, thrown)
                except StopAsyncIteration:
                    frame.closed = True
                    guard.ok()
                    return
                finally:
                    _FRAME.reset(token)
                delivered, thrown = True, None
                try:
                    sent = yield item
                except GeneratorExit:
                    aclose = getattr(stream, "aclose", None)
                    if aclose is not None:
                        await aclose()
                    raise
                except BaseException as exc:  # health: allow BLE001 -- forwarded into the wrapper's stream
                    sent, thrown = None, exc
        except BaseException as exc:  # health: allow BLE001 -- classified by _decide; RAISE re-raises it
            frame.closed = True
            if exc is thrown:
                raise
            untracked = guard.untracked_entries - before
            action = _decide(guard, frame, exc, delivered=delivered, can_return=False, untracked=untracked)
            if action == RAISE:
                raise
            caught = exc
        try:
            async for item in inner()(*args, **kwargs):
                yield item
        except Exception as again:
            if action == CHECKED:
                guard.settle(caught, again)
            raise
        if action == CHECKED:
            guard.settle(caught, None)
    return shell


_SHELLS = {SYNC: _sync_shell, ASYNC: _async_shell, GEN: _gen_shell, AGEN: _agen_shell}


def make_shell(guard: Guard, wrapped: Callable[..., Any], inner: Callable[[], Callable[..., Any]],
               flavor: str, original: Optional[Callable[..., Any]] = None) -> Callable[..., Any]:
    """The callable the registry installs for one patch: ``wrapped`` (the wrapper or the replacement)
    behind the safety shell. ``inner()`` is the next callable in the chain (ultimately the original).
    ``original`` (the target function) gives the shell its name, doc and signature."""
    return functools.wraps(original if original is not None else wrapped)(_SHELLS[flavor](guard, wrapped, inner))
