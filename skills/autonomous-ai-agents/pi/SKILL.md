---
name: pi
description: "Delegate coding to the Pi coding agent CLI (features, PRs)."
version: 1.0.0
author: Nguyen Cuong (ntcuong777)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Coding-Agent, Pi, Earendil, Code-Review, Refactoring]
    related_skills: [claude-code, codex, cursor, hermes-agent]
---

# Pi Coding Agent

Delegate coding tasks to [Pi](https://pi.dev) via the Hermes `terminal` tool. Pi is a minimal, extensible coding agent from earendil-works with a four-tool default loadout (read, bash, edit, write). It ships no permission system and never prompts — it runs with the permissions of the process that launched it, so containment is the caller's job.

## When to Use

- One-shot coding tasks: fix a bug, add a feature, refactor, write tests
- Read-only review: `--tools read,grep,find,ls`
- Automation over structured events (`--mode json`) or long-lived control (`--mode rpc`)
- Provider-agnostic work: 50+ providers, model switchable per run with `--model provider/id:thinking`

If Pi is not installed, use the `claude-code`, `codex`, or `cursor` skill instead.

## Prerequisites

- Install: `curl -fsSL https://pi.dev/install.sh | sh` (Windows: `powershell -c "irm https://pi.dev/install.ps1 | iex"`), or `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` (Node 22.19+)
- Auth: `pi auth login <provider>` (browser OAuth) or `pi auth login <provider> --api-key`. Provider env keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...) also resolve.
- Check auth: `pi auth check --provider <provider>` — exit `0` ready, `1` not ready, `2` invalid; `--json` for structure
- Version: `pi --version`; update: `pi update`

## How to Run

One-shot tasks (preferred). Print mode writes the final assistant text to stdout and exits; no PTY needed:

```
terminal(command="pi -p 'Fix the failing test in tests/test_auth.py'", workdir="~/project", timeout=300)
```

- Piped stdin is prepended to the first prompt: `git diff main | pi -p 'Review this diff'`
- `@path` attaches a text file or image to the first prompt
- Scratch run with no disk session: add `--no-session`
- Read-only review: `pi --tools read,grep,find,ls -p 'Review this project'`

Long tasks: launch in the background and monitor with the `process` tool:

```
terminal(command="pi -p 'Refactor the auth module'", workdir="~/project", background=true)
process(action="poll", session_id="<id>")
process(action="log", session_id="<id>")
```

Pi has no approval prompts and no `--max-turns` cap — a run ends when the model is done or at your `timeout`. Set the timeout deliberately.

Structured events: `pi --mode json "task" > events.jsonl` writes JSONL agent events, then exits. For continuous programmatic control (prompt, steer, follow-up, abort, model switching), run `pi --mode rpc` and speak JSONL over stdin/stdout.

## Model Selection

| Flag | Effect |
|---|---|
| `--model sonnet:high` | Fuzzy match with `:<thinking>` suffix |
| `--model anthropic/claude-...` | Exact `provider/id` |
| `--thinking off|minimal|low|medium|high|xhigh|max` | Override, clamped to model capability |
| `--list-models [search]` | List models, then exit |
| `--provider <name>` | Disambiguate when several providers offer the model |

## Interactive Sessions (tmux)

Pi is a fullscreen TUI. For multi-turn interactive work, drive it through tmux:

```
terminal(command="tmux new-session -d -s pi-work -x 140 -y 40")
terminal(command="tmux send-keys -t pi-work 'cd ~/project && pi' Enter")
terminal(command="sleep 3 && tmux send-keys -t pi-work 'Refactor the auth module' Enter")
terminal(command="tmux capture-pane -t pi-work -p -S -60")
terminal(command="tmux send-keys -t pi-work C-d")
```

While Pi works, typed input steers it: Enter steers the current run, Alt+Enter queues a follow-up for when it would stop, Alt+Up returns a queued message to the editor. Sessions branch: `/tree` and `/fork` navigate or branch, `/compact` compacts context, `/reload` reloads extensions. Resume headlessly with `pi -c` (most recent session in this directory) or `pi --session <path|id>`.

## Quick Reference

| Flag | Effect |
|---|---|
| `-p, --print` | One-shot: run prompts, print final text, exit |
| `--mode json` / `--mode rpc` | JSONL event stream / long-lived JSONL control protocol |
| `-c, --continue` | Continue the most recent session in this directory |
| `-r, --resume` / `--session <path\|id>` | Session picker / open by path or ID |
| `--session-id <id>` | Open or create the session with that ID |
| `--fork <path\|id>` | Fork a session into the current directory |
| `-n, --name <name>` | Set the session display name |
| `-t, --tools <list>` | Replace tool selection; `+name`/`-name` entries adjust it |
| `-xt, --exclude-tools <list>` | Disable tools by name or `*` pattern |
| `--no-mcp` / `-ne, --no-extensions` / `-ns, --no-skills` | Disable that resource class for this run |
| `--append-system-prompt <text\|path>` | Append to the system prompt (repeatable) |
| `--system-prompt <text\|path>` | Replace the system prompt |
| `-a, --approve` / `-na, --no-approve` | Trust / ignore project-local `.pi` config for this run |

## Pitfalls

1. **No permission system.** Pi never asks before any tool call. Contain the blast radius yourself: explicit `workdir`, clean git status, a throwaway `git worktree`, or a container for risky tasks (see Pi's `containerization.md` doc).
2. **Project trust gates local `.pi/` config.** In print mode pass `-a` to trust once or `-na` to ignore; interactively, approve the dialog.
3. **Plain `--tools` names replace the whole selection.** `pi --tools read,codemode` drops `bash`, `edit`, and `write`; use `+name`/`-name` entries to adjust instead.
4. **MCP tools survive `--tools`.** It only changes what is declared to the model; disable the server side with `--no-mcp` or `-xt 'mcp__*'`.
5. **Print output is final text only.** For tool calls, tokens, and usage, read the `--mode json` event stream instead.
6. **Interactive Pi needs a real TTY.** Use tmux; bare `pty=true` on the `terminal` tool lacks capture-pane and send-keys control.

## Verification

- Auth: `pi auth check --provider <provider> --json`
- Smoke: in a `mktemp -d` scratch directory, `pi -p 'Reply with ok'`
- Audit: rerun the task with `--mode json` and confirm the JSONL stream reaches the expected terminal event
