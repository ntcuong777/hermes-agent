/**
 * The slash-command wire contract every TypeScript surface shares: how a typed
 * line splits into `name` + `arg`, and the `command.dispatch` response union
 * (`tui_gateway/methods_tools.py::command.dispatch`). One parser, so a fix such
 * as the multi-line argument bug (#41323, #55510) reaches desktop and TUI at
 * once instead of landing in whichever copy the reporter happened to use.
 */

/** A slash COMMAND invocation: `/` at position 0, a bare name, then whitespace
 *  or end. `/usr/local` (second slash) and `run /clean` (not at 0) are prose. */
export const SLASH_COMMAND_RE = /^\/[^\s/]*(?:\s|$)/

export const looksLikeSlashCommand = (text: string): boolean => SLASH_COMMAND_RE.test(text)

// `[\s\S]*` (not `.*`): the arg may span newlines — `/goal <multi-line text>`
// or a skill command with a long pasted context. A `.*$` regex fails the
// whole match on any newline, so every multiline slash command parsed as an
// empty name and got swallowed. The backend and CLI both split on any
// whitespace (`split(maxsplit=1)`), so `\S+` for the name is the parity rule.
// Only the separator between name and argument is whitespace the parser owns;
// the argument's interior (runs of spaces, indentation, blank lines) survives
// verbatim — flattening it once turned every pasted diff into one line.
const SLASH_PARTS_RE = /^(\S+)([\s\S]*)$/

export interface ParsedSlashCommand {
  /** Argument text with the outer whitespace removed; interior kept verbatim. */
  arg: string
  /** Lower-cased command name without the slash; '' for `/`, `/   `, `/ words`. */
  name: string
}

/** Split `/name arg…` the way the backend does. The name is lower-cased because
 *  every consumer (`hermes_cli.commands.resolve_command`, `slash.exec`) is
 *  case-insensitive; keeping the typed case only made surfaces disagree. */
export function parseSlashCommand(command: string): ParsedSlashCommand {
  const match = SLASH_PARTS_RE.exec(command.replace(/^\/+/, ''))

  return match ? { arg: match[2]!.trim(), name: match[1]!.toLowerCase() } : { arg: '', name: '' }
}

export interface ExecCommandDispatchResponse {
  output?: string
  type: 'exec' | 'plugin'
}

export interface AliasCommandDispatchResponse {
  target: string
  type: 'alias'
}

export interface SkillCommandDispatchResponse {
  /** The invocation the UI renders (`/work fix the leak`). `message` is the
   *  expanded skill body — model-facing scaffolding no surface may show. */
  display?: string
  message?: string
  name: string
  type: 'skill'
}

export interface SendCommandDispatchResponse {
  /** Set for a skill-bundle send: see SkillCommandDispatchResponse.display. */
  display?: string
  message: string
  notice?: string
  type: 'send'
}

export interface PrefillCommandDispatchResponse {
  /** A slash command to run once the client has actually put `message` in the composer. */
  ack?: string
  message: string
  notice?: string
  type: 'prefill'
}

/** Empty the composer (a keybind's save went through). */
export interface ComposerClearCommandDispatchResponse {
  notice?: string
  type: 'composer-clear'
}

/** A picker. Enter (or 1-9) runs the row's `select` command, `x` its `drop`; each answers with a directive. */
export interface ChoicesCommandDispatchResponse {
  items: { drop?: string; label: string; select: string }[]
  /** Shown as a system line when the picker opens (what a `drop` just did). */
  notice?: string
  selected?: number
  title: string
  type: 'choices'
}

export type CommandDispatchResponse =
  | AliasCommandDispatchResponse
  | ChoicesCommandDispatchResponse
  | ComposerClearCommandDispatchResponse
  | ExecCommandDispatchResponse
  | PrefillCommandDispatchResponse
  | SendCommandDispatchResponse
  | SkillCommandDispatchResponse

const str = (value: unknown) => (typeof value === 'string' ? value : undefined)

const choiceItems = (value: unknown): ChoicesCommandDispatchResponse['items'] | null => {
  const rows = Array.isArray(value) ? (value as Record<string, unknown>[]) : []

  return rows.length && rows.every(r => r && typeof r.label === 'string' && typeof r.select === 'string')
    ? rows.map(r => ({ drop: str(r.drop), label: r.label as string, select: r.select as string }))
    : null
}

/** Narrow a raw `command.dispatch` result to the union, or null when the
 *  payload is malformed (missing required field, unknown `type`). */
export function parseCommandDispatch(raw: unknown): CommandDispatchResponse | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return null
  }

  const row = raw as Record<string, unknown>

  switch (row.type) {
    case 'alias':
      return typeof row.target === 'string' ? { target: row.target, type: 'alias' } : null

    case 'choices': {
      const items = choiceItems(row.items)

      return items && typeof row.title === 'string'
        ? {
            items,
            notice: str(row.notice),
            selected: typeof row.selected === 'number' ? row.selected : undefined,
            title: row.title,
            type: 'choices'
          }
        : null
    }

    case 'composer-clear':
      return { notice: str(row.notice), type: 'composer-clear' }

    case 'exec':

    case 'plugin':
      return { output: str(row.output), type: row.type }

    case 'prefill':
      return typeof row.message === 'string'
        ? { ack: str(row.ack), message: row.message, notice: str(row.notice), type: 'prefill' }
        : null

    case 'send':
      return typeof row.message === 'string'
        ? { display: str(row.display), message: row.message, notice: str(row.notice), type: 'send' }
        : null

    case 'skill':
      return typeof row.name === 'string'
        ? { display: str(row.display), message: str(row.message), name: row.name, type: 'skill' }
        : null

    default:
      return null
  }
}
