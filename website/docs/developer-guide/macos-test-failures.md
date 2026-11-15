# Known test failures on macOS (fork audit, 2026-11-15)

A full-suite audit of the `personal` branch on a macOS (darwin 25.6.0, arm64)
host classified every failing test as **fork-caused** or **upstream/machine**.
All fork-caused failures were fixed; what remains is documented here so the
next person does not re-derive it. Nothing in this list is caused by a
fork-only patch: every root was attributed by reading the failing code path
and, where it mattered, checking `git log -S` against the fork's commits.

The fork's own CI (`fork-check.yml`) runs `ubuntu-latest`, so none of these
block the checks a fork patch must pass. Upstream CI is also Linux-only.

## How the attribution was done

- Full suite via `scripts/run_tests.sh` with an isolated
  `HERMES_HOME`/`HERMES_RUNTIME_DIR`, then every failing file re-run with
  full tracebacks captured.
- Per failure: read the traceback, locate the production code involved,
  `git log main..personal -S "<symbol>"` to see whether a fork commit
  introduced or moved it, and — for machine-dependent failures — reproduce
  the mechanism standalone (e.g. psutil probes) rather than trusting the
  assertion text.
- After the fixes, the consumer sweep re-ran every test file that imports a
  production module touched by a fix: 199 files, 4,416 passed, 0 unexpected
  failures.

Test environment notes that matter for reproduction:

- Run with the test home **outside `/tmp`** (the suite's runtime self-delete
  guard hard-blocks `rm -rf /tmp` because the PM venv lives under it —
  `tests/tools/test_command_guards.py::test_dangerous_command_deny` fails
  from `/tmp` for that reason alone).
- The host machine runs a real production gateway out of `~/.hermes`.
  `hermes update` tests enumerate gateways on the host and deliberately
  leave *another home's* gateway alone, which changes their exit paths.

## The 150 remaining failures

### 72 — `tests/hermes_state/*` (macOS process probing)

Root cause, two layers:

1. `hermes_state_holders.foreign_state_db_holders` uses
   `psutil.process_iter(["pid", "open_files"])` on non-Linux. On macOS,
   psutil reports **no open files** (verified standalone: a live
   `sqlite3` holder process is invisible), so every "prove the store is
   quiet before surgery" scan returns empty and the guarded operation
   proceeds until SQLite itself refuses (`database is locked`), or a
   live-writer guard trips where the test expects it not to.
2. Several suites test Linux-only mechanisms directly: `/proc/<pid>/fd`
   walks, `/proc/self/mountinfo` parsing (`_detect_network_fs`,
   `_detect_cross_vm_fs` — always `False` on macOS), and FTS/vacuum
   maintenance flows that depend on the holder scans.

Affected: `test_state_db_malformed_repair` (16),
`test_state_db_repair_non_destructive` (14), `test_hermes_state` (8),
`test_state_db_repair_loop_cap` (5), `test_fts_optimize_progress` (3),
`test_network_fs_wal_warn` (3), `test_cross_vm_fs_wal_refusal` (3),
`test_fts_rebuild_admission` (3), `test_fts_runtime_rebuild` (2), and one
each in `test_auto_vacuum_holder_gate`, `test_auto_vacuum_in_process_holder_gate`,
`test_fts_fresh_bootstrap_admission`, `test_guest_durability_barriers`,
`test_network_fs_journal_default`, `test_repair_unavailable_health`,
`test_startup_maintenance_lease`, `test_state_db_fts_segment_collision_probe`,
`test_state_db_repair_live_writer_guard`, `test_state_db_second_process_maintenance`,
`test_state_db_repair_loop_mtime`, `test_state_db_wal_unlink_race`,
`test_state_db_maintenance_conformance`, `test_wal_checkpoint_strategy`.

Upstream would need to either gate these `@pytest.mark.platforms("linux")`
or back the macOS scan with a non-psutil mechanism. Not a fork concern.

### 29 — `tests/hermes_cli/test_update_*` (host state + launchd)

- The update flow lists running gateways before restarting anything. On this
  host it finds the **real production gateway** (`~/.hermes`) and correctly
  leaves it alone ("PID …: gateway process of another Hermes home — left
  alone"), which diverts the flow from the path the test asserts (expected
  exit code 3 / marker cleared, got 1).
- On macOS the same flow talks to `launchctl` (`launchctl print gui/$(id -u)/…`);
  several tests stub `subprocess.run` with a guard that asserts **only `git`
  is ever invoked**, so the launchctl probe fails the stub on darwin by
  design (upstream CI never executes the darwin branch).

Affected: `test_update_target_identity` (11), `test_update_autostash` (8),
`test_update_fleet_restart_pending` (7), `test_update_interrupted_pull` (1),
`test_update_zip_webapp_preserve` (2 errors — `git clone --shared` against a
fixture whose origin resolves under this checkout's path).

### 14 — holder-gated CLI flows (same psutil root as `hermes_state`)

`sessions delete/prune/held-store`, `sessions set-journal-mode`, `hermes doctor`
WAL/journal checks, `kanban_db` spawn-refusal accounting: each proves
"nothing else holds state.db" through the same psutil-backed scan. On macOS
the scan is blind, so refusals happen at the SQLite layer (or not at all)
instead of through the holder list the tests assert on.

### 6 — `tests/tui_gateway/test_tui_gateway_server.py` (darwin sidecar scan)

`prompt.submit` on a session whose store lives on a deleted-sidecar-risk path
spawns the macOS deleted-sidecar holder scan
(`hermes_state_dbfile._iter_darwin_sidecar_holders`), which runs its work in a
`threading.Thread`. The tests capture `threading.Thread` with a simple fake and
then run `threads[0].target()` expecting the prompt turn thread — on darwin the
scan thread is created first, so `threads[0]` is the scan. Reproduced
standalone: `threads[0].target` is
`_iter_darwin_sidecar_holders.<locals>._scan`; the real turn thread is
`threads[1]`, `running` stays `True`, six assertions fail. Linux never
creates the scan thread, so upstream CI is green.

### 5 — `tests/pm/*` (Linux cross-target)

`test_pm_bionic` and `test_stage_only` stage Android/bionic artifacts and
assert the host does not execute foreign bytes; on macOS the sandbox probe
differs (codesign path). `test_chromium` expects `chromium` in
`default_package_names()`; `test_declined_extras` depends on the same
installed-package set. Machine-provisioning dependent.

### 3 — `tests/agent/test_compaction_prompt_rebuild.py` (path symlink)

The tests build the workspace under pytest's temp root (`/var/tmp/...`) and
assert the system prompt carries that literal path as `Root:`. Production
resolves the workspace with `realpath`, and on macOS `/var` is a symlink to
`/private/var`, so the prompt says `/private/var/...`. Same assertion would
pass on Linux where the prefix is not a symlink.

### 2 — missing optional test dependencies

`tests/agent/test_vault_key_storage.py`, `tests/hermes_cli/test_vault_key_cli.py`
(`ModuleNotFoundError: No module named 'keyring'`) and
`tests/tools/test_fal_sdk.py` (`No module named 'fal_client'`) fail at
collection. The declared test group on this install does not carry them;
upstream CI installs the extras.

### 7 — `tests/tools/test_sudo_askpass_delivery.py` (host sudo policy)

Drives the real `sudo` + askpass pipeline; this host's sudo policy prompts
for a password ("Sorry, try again"), so delivery fails. Upstream CI's runner
has non-interactive sudo.

### 2 — `tests/hermes_cli/test_desktop_update_verify.py` (desktop freshness receipt)

The fixture stamps the bundle under the test's environment and
`_desktop_build_needed` then reports the build stale on this host
(freshness receipt env/platform mismatch on darwin). The same fixture passes
on Linux CI; the darwin branch of the freshness gate is not exercised there.

### 2 — `tests/hermes_cli/test_gateway_runtime_ensure.py` (launchd path)

`spawn_unmanaged_gateway` takes the darwin `start_gateway_job` branch, which
probes `launchctl` through `subprocess.run`; the test replaces
`subprocess.Popen` globally, so the probe dies inside the stdlib's own
`run()`. Linux skips the branch entirely.

### 1 each — assorted darwin/environment

- `tests/gateway/test_session_client_env.py::test_auto_started_gateway_does_not_inherit_client_behaviour_env`
  — same launchd/Popen-stub shape as above.
- `tests/hermes_cli/test_shared_metrics_harness.py::test_foreground_terminal_commands_count_by_kind_and_outcome`
  — asserts `ls /definitely/not/here` exits 2 (GNU ls); BSD ls exits 1.
- `tests/hermes_cli/test_uninstall_full_wipe_sweep.py::test_remove_dashboard_launchd_jobs_boots_out_and_deletes_matching_plists`
  — launchd job enumeration/removal differs on the host.
- `tests/hermes_cli/test_plugin_validate.py::test_portable_validation_fails_orphan_and_reports_availability`
  — on darwin the availability detail is `missing_app, path <abs>`; the test
  pins the bare string, which is what the Linux path (`unsupported_os`) yields.
- `tests/hermes_cli/test_desktop_swap_file_lock_retry.py::test_swap_retries_transient_permission_error_then_promotes`
  — the rename-lock stub matches the flat `linux-unpacked`/`win-unpacked`
  layout; the darwin bundle is nested (`mac-arm64/Hermes.app/Contents/MacOS`),
  so the stub never fires.
- `tests/tools/test_transcription_tools.py::…` — faster-whisper's default
  `compute_type` differs on this install (`int8` vs the pinned `float32`).
- `tests/tools/test_session_tool_env.py::…` — a `$PATH`-relative probe
  resolves through the host's nvm dir instead of the fixture directory.
- `tests/tools/test_process_registry_pty_kill.py` (2) and
  `test_process_registry_env_poller.py` — PTY/exit-polling timing races.
- `tests/tools/test_bot_desktop_resources.py::…` — expects a running Xvnc
  desktop session (Linux).
- `tests/agent/transports/` cell for `cursor` needed a recorded wire; it was
  recorded during the fix pass (`HERMES_UPDATE_WIRE_SNAPSHOT=1`), and that
  file's suite is green now.

### 1 — this checkout's own location

`tests/tools/test_file_tools.py::TestPatchHints::test_no_match_includes_hint`
patches a *relative* path (`foo.py`), which resolves against the repo — and
this checkout lives at `/etc/nix-darwin/fork/hermes-agent`, under the
`write_file`/`patch` system-deny prefix. The refusal fires before the
not-found hint path the test wants. `security.write_allow_roots` exempts
this exactly, but `tests/conftest.py` always rewrites `HERMES_HOME` to a
throwaway home, so a config-based exemption cannot reach the test. On any
normal checkout path the test passes. (The fork added
`security.write_allow_roots` for precisely this checkout; the feature's own
suite, `tests/tools/test_file_write_allow_roots.py`, is green.)

## Fork-caused failures that were fixed (for the record)

1. `api_server_openai_routes` lazily imported `_chat_usage_payload` from
   `api_server` after a code-health move had relocated it — every
   OpenAI-compatible SSE stream 500ed. Self-import removed.
2. `hermes_cli/gateway.py` carried two dead inline copies of the restart-watcher
   source (superseded by `watcher_source()`) containing raw `**os.environ`
   spawns; deleted, which also cleared the spawn-env lint guard.
3. `model_switch_model_ids._effective_model_candidate` had a branch-local
   `_clean` import → `UnboundLocalError` on attr-object candidates.
4. `session_authorities.authority_for_profile_id` refused the canonical
   bare label `default` that webhook/API grant flows legitimately carry;
   bare names now resolve through served homes only (cwd-free), unknown
   names still raise (the bare-name contract test from the fork still holds).
5. Locale parity: the `/cwd` feature, `gateway.compress.started`,
   `gateway.exec_approval.mcp_header`, the Discord `cwd` argument hint and
   14 TUI keys were English-only; translations added for all 16 non-English
   catalogs and all 16 TUI packs, and the 17 renamed `gateway.topic.*` keys
   were restored to `en.yaml` (production still emits them for the Telegram
   topic flow).
6. A merge had dropped the cursor SDK runtime resolution
   (`_maybe_apply_cursor_sdk_runtime`, `cursor_sdk` in `_VALID_API_MODES`,
   and its call site); restored from the original commit.
7. `agent/cursor_runtime` used a bare-PATH `which("node")`; now
   `hermes_constants.find_node_executable("node")` with the legacy fallback.
8. `hermes_cli/config_defaults` re-exports `OPTIONAL_ENV_VARS` (old-updater
   mid-swap contract).
9. `scripts/ci/prompt_surface.py` scrubs both spellings of the temp home
   (macOS symlink), and the prompt-surface snapshot + desktop slash
   registry were regenerated for the fork's surface changes.
10. `scripts/boundary_lint_baseline.json` updated (`--update`) for the
    fork's cursor-plugin and discord adapter crossings. If these crossings
    should ever be eliminated properly, the fix is plugin-API surface, not
    the baseline.
11. `hermes_cli/commands_platforms.py`: fork commands tipped Slack's
    50-slash cap, silently dropping `/usage`; `topic`/`sethome`/`bundles`
    demoted to `_SLACK_VIA_HERMES_ONLY` per the file's own rule.
12. Stale test pins updated where a fork contract deliberately moved:
    trajectory import path, memory-precheck wording, deferred-tool hint,
    `enabled_tools` superset, the messaging-surface `/model --global`
    write target (with a new CLI-surface test keeping the #25107 regression
    covered), one missed `_CanonicalOwner.sessions` fake.
