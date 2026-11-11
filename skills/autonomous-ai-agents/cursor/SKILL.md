---
name: cursor
description: "Delegate coding to the Cursor Agent CLI (features, PRs)."
version: 1.0.0
author: Nguyen Cuong (ntcuong777)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Coding-Agent, Cursor, Code-Review, Refactoring]
    related_skills: [claude-code, codex, pi, hermes-agent]
---

# Cursor Agent

Delegate coding tasks to [Cursor's CLI](https://cursor.com/docs/cli/reference/parameters) via the Hermes `terminal` tool. The binary is `cursor-agent` (Cursor's docs also call it `agent`), and print mode (`-p`) has access to all tools, including file writes and shell commands.

## When to Use

- One-shot coding tasks under a Cursor subscription or `CURSOR_API_KEY`
- Read-only analysis with `--mode plan` (propose, no edits) or `--mode ask` (Q&A)
- Isolated experiments in managed worktrees with `-w`
- Machine-readable results with `--output-format json`

If Cursor is not installed or authenticated, use the `claude-code`, `codex`, or `pi` skill instead.

## Prerequisites

- Install: `curl https://cursor.com/install -fsS | bash`
- Auth: `cursor-agent login` (set `NO_OPEN_BROWSER=1` on headless hosts), or pass `--api-key <key>` / export `CURSOR_API_KEY`
- Sign out: `cursor-agent logout`
- Version: `cursor-agent --version`; models: `cursor-agent --list-models` (also proves auth and connectivity)

## Approval Semantics (verified on 2026.11.10-731fb78)

Print mode fails closed until you opt in. In a fresh directory:

| Flags | Behavior |
|---|---|
| `-p` only | Exits `1` with a "Workspace Trust Required" error |
| `-p --trust` | Runs, but shell commands are auto-rejected ("Rejected:", nothing executes), exit `0` |
| `-p --trust -f` | Commands execute unless explicitly denied |

For unattended one-shots use `-p --trust -f` (`--yolo` is an alias of `-f`) and contain the blast radius yourself. `--auto-review` auto-runs classifier-approved safe calls but prompts for the rest, so its prompts cannot be answered in `-p` — reserve it for interactive runs.

## How to Run

One-shot tasks (preferred). Print mode needs no PTY:

```
terminal(command="cursor-agent -p --trust -f 'Fix the failing test in tests/test_auth.py'", workdir="~/project", timeout=300)
```

- Read-only: `cursor-agent -p --trust --mode plan 'Evaluate options for the auth refactor'`
- Structured: add `--output-format json` (single result) or `stream-json` plus `--stream-partial-output` for deltas
- Long tasks: `background=true` on the `terminal` call, then monitor with the `process` tool:

```
terminal(command="cursor-agent -p --trust -f 'Refactor the auth module'", workdir="~/project", background=true)
process(action="poll", session_id="<id>")
process(action="log", session_id="<id>")
```

## Model Selection

| Flag | Effect |
|---|---|
| `--model gpt-5`, `--model sonnet-4-thinking` | Pick a model |
| `'claude-opus-4-8[context=1m,effort=high,fast=false]'` | Parameterized bracket overrides |
| `--list-models` | List available models, then exit |

## Sessions and Worktrees

| Flag | Effect |
|---|---|
| `--continue` | Continue the previous session (alias of `--resume=-1`) |
| `--resume [chatId]` | Resume a specific session (interactive picker when omitted) |
| `-w, --worktree [name]` | Isolated worktree under `~/.cursor/worktrees/<reponame>/<name>` |
| `--worktree-base <branch>` | Base the new worktree on another ref |
| `--workspace <path>` / `--add-dir <path>` | Set or widen the workspace roots |

For work that must survive a dropped terminal, `cursor-agent persist` starts a disconnect-safe session.

## Interactive Sessions (tmux)

Same pattern as the `codex` skill: start a tmux session with the `terminal` tool, `tmux send-keys` the task, monitor with `tmux capture-pane -t <session> -p -S -60`, and send `C-d` when done. `--trust` also skips the interactive workspace-trust dialog; `--approve-mcps` auto-approves MCP servers.

## Pitfalls

1. **Fresh directories fail closed.** Print mode exits `1` with "Workspace Trust Required" until you pass `--trust` (a headless-only flag), `-f`, or `--yolo`.
2. **`--trust` alone does not allow commands** — they come back "Rejected:". Pair it with `-f` for unattended builds.
3. **`-f` is a real bypass.** Use a clean git status or a `-w` worktree as the safety layer, then review `git diff`; worktree directories live under `~/.cursor/worktrees/` and need cleanup.
4. **`--sandbox enabled` restricts execution**; explicit `--sandbox disabled` restores the default allowlist behavior if a sandboxed run misbehaves.
5. **`--resume` without an ID opens an interactive picker** — pass a chat ID (or use `--continue`) in scripts.

## Verification

- Auth + connectivity: `cursor-agent --list-models`
- Smoke: in a `mktemp -d` scratch directory, `cursor-agent -p --trust -f 'Reply with ok'`
- Audit: rerun with `--output-format json` and parse the result record
