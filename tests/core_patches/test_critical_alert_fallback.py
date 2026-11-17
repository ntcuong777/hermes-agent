"""A critical alert with problems but no notice lines still sends one meaningful line to the home channel
(hermes_cli/core_patches_report.py critical_alert)."""

from __future__ import annotations

import logging

from hermes_cli import core_patches_policy as policy
from hermes_cli import core_patches_report as report
from tests.core_patches.test_failsafe_report import surfaces_gateway  # noqa: F401 -- the fake sender fixture


def test_a_critical_alert_with_problems_and_no_notice_lines_sends_exactly_the_fallback_line(
        surfaces_gateway, monkeypatch, tmp_path):
    monkeypatch.setattr(policy, "safe_state_dir", lambda: tmp_path)
    monkeypatch.setattr(policy, "load_settings", lambda: policy.Settings())
    monkeypatch.setitem(report._STATE, "surface", "gateway")
    report.critical_alert(["demo:up is skipped-error (critical)"], [])
    assert len(surfaces_gateway) == 1
    bullets = [line for line in surfaces_gateway[0].splitlines() if line.startswith("- ")]
    assert bullets == ["- a critical core patch is not installed"]
