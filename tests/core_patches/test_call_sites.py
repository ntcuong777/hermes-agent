"""The registry's footprint in upstream-owned files: exactly these one-line call sites.

Each line carries `# fork: core-patch registry` except the class line and the doctor tuple entry.
If an upstream merge moves one of these blocks, this test names the file to re-apply the line in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SAFE = '__import__("hermes_cli.core_patches_safe").core_patches_safe'
APPLY = f"{SAFE}.apply"

CALL_SITES = [
    ("hermes_cli/main.py", f'        {APPLY}("cli")  # fork: core-patch registry'),
    ("hermes_cli/main.py", f'        {APPLY}("dashboard")  # fork: core-patch registry'),
    ("gateway/run_startup.py", f'        {SAFE}.gateway_apply()  # fork: core-patch registry'),
    ("tui_gateway/entry.py", f'    {APPLY}("tui_gateway")  # fork: core-patch registry'),
    ("cron/scheduler.py", f'        {SAFE}.cron_apply()  # fork: core-patch registry'),
    ("cron/scheduler.py", f'    {SAFE}.cron_dispatch_guard(_get_hermes_home())  # fork: core-patch registry'),
    ("hermes_cli/plugins_context.py", "from hermes_cli.core_patches_ctx import CorePatchCtxMixin  # fork: core-patch registry"),
    ("hermes_cli/plugins_context.py", "class PluginContext(PluginCapabilityCtxMixin, PluginIdleTaskCtxMixin, CorePatchCtxMixin):"),
    ("hermes_cli/subcommands/plugins.py", f"    {SAFE}.add_patches_parser(plugins_subparsers)  # fork: core-patch registry"),
    ("hermes_cli/plugins_cmd.py", f'    "patches": lambda args: {SAFE}.cmd_patches(args),  # fork: core-patch registry'),
    ("hermes_cli/doctor.py", f"_check_core_patches = {SAFE}.doctor_check  # fork: core-patch registry"),
    ("hermes_cli/doctor.py", "    (None, _check_core_patches),"),
    ("hermes_cli/plugin_validate.py",
     "from hermes_cli.plugin_validate_core_patches import check_core_patches  # fork: core-patch registry"),
    ("hermes_cli/plugin_validate.py", "    check_core_patches(report, manifest, plugin_dir)"),
]


@pytest.mark.parametrize("path, line", CALL_SITES, ids=[f"{p}:{i}" for i, (p, _) in enumerate(CALL_SITES)])
def test_the_call_site_is_present_once(path, line):
    lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
    assert lines.count(line) == 1, f"{path}: expected exactly one line {line!r}"


def test_the_cli_call_runs_after_background_discovery_starts():
    text = (ROOT / "hermes_cli/main.py").read_text(encoding="utf-8")
    assert text.index("start_background_plugin_discovery()") < text.index(f'{APPLY}("cli")')


def test_the_cron_guard_runs_before_the_handoff_is_marked():
    text = (ROOT / "cron/scheduler.py").read_text(encoding="utf-8")
    start = text.index("def _launch_external_cron_worker(")
    guard = text.index(f"{SAFE}.cron_dispatch_guard(", start)
    assert text.index('if dispatch.mode == "in_process":', start) < guard < text.index(
        "mark_execution_handoff_pending(execution_id)", start)


def test_the_tui_call_runs_before_the_ready_event():
    text = (ROOT / "tui_gateway/entry.py").read_text(encoding="utf-8")
    assert text.index(f'{APPLY}("tui_gateway")') < text.index('"type": "gateway.ready"')
