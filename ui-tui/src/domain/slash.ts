/** Appended by TUI pickers; converted to the backend's `--session` flag before `config.set`. */
export const TUI_SESSION_MODEL_FLAG = '--tui-session'

/** The levels `hermes_constants.parse_reasoning_effort` accepts; the display words are config.yaml
 *  writes. */
export const CANONICAL_REASONING_EFFORTS = new Set([
  'none',
  'false',
  'disabled',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
  'ultra'
])

export const sessionScopedModelArg = (value: string) => {
  const parts = value.trim().split(/\s+/).filter(Boolean)
  const kept = parts.filter(part => part !== TUI_SESSION_MODEL_FLAG && part !== '--global' && part !== '--session')

  return kept.length ? `${kept.join(' ')} --session` : ''
}

// A `/` means two different things depending on where it sits:
//
//  - At position 0 it's a COMMAND invocation the TUI executes
//    (`looksLikeSlashCommand`, and the backend's dispatch, are both
//    `^`-anchored). Completion stays live past the command name so arg
//    completion works (`/cron ad` → `add`).
//  - After whitespace it's an inline SKILL reference the user is dropping into
//    prose ("clean this up with /clean"). The text submits as an ordinary
//    message, so there are no args to complete — the token ends at the caret.
//
// The inline shape is what makes skills reachable anywhere in a prompt. The
// trailing `$` matters for both: completion runs against the text the user has
// typed so far, so the match has to end where they're typing.
//
// Requiring a single bare segment (no second `/`) keeps real paths out:
// `look at /usr/local/bin` and `check src/foo/bar` never match. A bare `/us` is
// genuinely ambiguous with an absolute path, and resolves as a skill reference
// — typing the next `/` flips it straight back to path completion.
const INLINE_SLASH_RE = /\s\/([a-zA-Z][\w-]*)?$/

/**
 * Locate an inline `/skill` reference at the end of `text`, or null when the
 * text isn't one. `start` is the index of the `/` itself, so a completion
 * replaces the typed token and leaves the prose in front of it untouched.
 */
export const inlineSlashTrigger = (text: string): { query: string; start: number } | null => {
  const match = INLINE_SLASH_RE.exec(text)

  if (!match) {
    return null
  }

  const query = match[1] ?? ''

  return { query, start: text.length - query.length - 1 }
}

/**
 * Apply a completion row to the current input, mirroring the editor's
 * replace semantics: replace from `compReplace` with the row text, dropping
 * the row's leading slash when the input already has one immediately before
 * the replace point (the gateway's slash completer returns bare command names
 * whose replace span begins after the leading `/`).
 *
 * Keyed off the character before `compReplace` rather than the start of the
 * input so a `/token` anywhere in the message behaves the same as one at
 * position 0 — an inline `run /cle` replaces from after its own slash.
 */
const completionText = (value: string, rowText: string, compReplace: number): string =>
  value[compReplace - 1] === '/' && rowText.startsWith('/') ? rowText.slice(1) : rowText

/**
 * Where the word being completed ends NOW: the live caret when the composer recorded it for this very text, else the
 * end. Never the span recorded when the completion was requested: its rows arrive a moment later, and what was typed
 * in between belongs to the word (a stale end left it behind: `/writing-skillst` after picking `/writing-skills`).
 */
export const completionEnd = (value: string, caret: null | undefined | { cursor: number; value: string }): number =>
  caret?.value === value ? Math.max(0, Math.min(value.length, caret.cursor)) : value.length

/** `replaceTo` is where the replaced word ends (`completionEnd`). Text after it stays (the person typed `@` mid-prompt). */
export const applyCompletion = (
  value: string,
  rowText: string,
  compReplace: number,
  replaceTo: number = value.length
): string => {
  const end = Math.min(value.length, Math.max(compReplace, replaceTo))

  return value.slice(0, compReplace) + completionText(value, rowText, compReplace) + value.slice(end)
}

/** Where the caret goes after `applyCompletion`: right after the inserted text. */
export const completionCursor = (value: string, rowText: string, compReplace: number): number =>
  compReplace + completionText(value, rowText, compReplace).length

/**
 * Decide what Enter does when a completion is highlighted: returns the value
 * to set (accept the completion) or `null` to fall through to submit.
 *
 * Enter accepts a completion only when it changes the command/argument token.
 * A completion that merely appends trailing whitespace to an already-complete
 * command (e.g. `/exit` → `/exit `, the trailing space the gateway adds so the
 * classic CLI's prompt_toolkit dropdown stays open) must NOT swallow the Enter
 * — otherwise every slash command needs an extra keypress: type → Enter
 * completes the name → Enter adds the space → Enter finally submits. Treating a
 * whitespace-only delta as "already complete" collapses that back to the
 * expected one/two presses.
 */
export const completionToApplyOnSubmit = (
  value: string,
  rowText: string | undefined,
  compReplace: number,
  isLocalCommand: (name: string) => boolean = () => false,
  replaceTo: number = value.length
): string | null => {
  // The gateway does not know TUI-local commands, so its fuzzy list can rank another command
  // above one typed in full (`/connectors` matches a description word of `/login`).
  const typed = value.trim()

  if (!rowText || (/^\/[\w-]+$/.test(typed) && isLocalCommand(typed.slice(1)))) {
    return null
  }

  const next = applyCompletion(value, rowText, compReplace, replaceTo)

  return next !== value && next.trimEnd() !== value.trimEnd() ? next : null
}
