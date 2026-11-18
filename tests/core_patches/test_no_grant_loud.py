"""A critical patch skipped for a missing grant is LOUD (an ERROR log, the alert, the home-channel notice) but never a
startup refusal by itself (review B15-1). A non-critical patch with no grant stays as quiet as before."""

from __future__ import annotations

import logging

from hermes_cli import core_patches as cp
from tests.core_patches.test_registry import _install, _spec, up


def test_the_missing_grant_is_loud_but_not_in_the_refusal_set():
    assert cp.SKIPPED_NO_GRANT in cp.LOUD_STATUSES
    assert cp.SKIPPED_NO_GRANT not in cp.REFUSE_STATUSES


def test_a_critical_patch_with_no_grant_is_in_the_problems_and_the_notice(target, no_discovery, caplog):
    caplog.set_level(logging.ERROR, logger="hermes_cli.core_patches")
    _install(_spec(target, "must", up, critical=True), granted=False)
    problems = cp.critical_problems()
    assert any("demo:must" in line and "skipped-no-grant" in line for line in problems), problems
    notice = cp.critical_notice_lines()
    assert any(line.startswith("demo:must is skipped-no-grant") for line in notice), notice


def test_a_critical_patch_with_no_grant_never_refuses_startup_even_with_refuse_on_critical(
        target, no_discovery, refusing):
    _install(_spec(target, "must", up, critical=True), granted=False)
    assert cp._refused(cp.records()) == []  # the refusal set is untouched by a missing grant
    records = cp.apply_core_patches("test")  # must return: no CorePatchRefusal
    assert [r.status for r in records] == [cp.SKIPPED_NO_GRANT]


def test_a_non_critical_patch_with_no_grant_is_not_loud(target, no_discovery):
    _install(_spec(target, "quiet", up), granted=False)
    assert cp.critical_problems() == []
    assert cp.critical_notice_lines() == []
