"""Generators: once a generator wrapper has started the original stream, a failure propagates and is not
counted (hermes_cli/core_patches_shell.py, module docstring, 'Generators')."""

from __future__ import annotations

import pytest

from hermes_cli import core_patches_shell as shell


def test_a_wrapper_that_started_the_original_stream_and_fails_on_its_first_chunk_propagates_uncounted():
    def count(n):
        yield from range(n)

    def make(original):
        def wrapper(n):
            stream = original(n)
            next(stream)  # the original stream is started and its first item taken
            raise RuntimeError("first chunk")
            yield  # pragma: no cover -- makes it a generator function
        return wrapper

    guard = shell.Guard("t:gen", "wrap", 3, (), None)

    def inner():
        return count

    candidate = make(shell.trampoline(guard, inner, count, shell.GEN))
    f = shell.make_shell(guard, candidate, inner, shell.GEN)
    with pytest.raises(RuntimeError, match="first chunk"):
        list(f(3))
    assert guard.total == 0
