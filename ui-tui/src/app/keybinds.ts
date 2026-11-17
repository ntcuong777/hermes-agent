import type { Key } from '@hermes/ink'
import type { SlashExecResponse } from '@hermes/shared/gateway-types'
import { parseCommandDispatch } from '@hermes/shared/slash'
import { atom } from 'nanostores'

import type { GatewayClient } from '../gatewayClient.js'
import { rpcErrorMessage } from '../lib/rpc.js'

import { showChoices } from './choicePicker.js'
import type { InputHandlerContext } from './interfaces.js'
import { $slashCatalog, loadSlashCatalog, setSlashCatalog } from './slashCatalog.js'
import { turnController } from './turnController.js'
import { getUiState } from './uiStore.js'

/**
 * Key chords that run a slash command (`display.tui_keybinds`, plus the defaults a plugin declares with
 * `register_command(keybinds=)`). The command gets the live composer text as its `composer_text` keyword
 * and answers with a plain line or a directive: `prefill` (replace the composer, then run its `ack`),
 * `composer-clear`, `choices` (a picker whose rows run commands, each answering the same way).
 */

export interface Chord {
  alt: boolean
  ctrl: boolean
  key: string
  shift: boolean
}

export interface Keybind {
  chord: Chord
  command: string
}

const MODIFIERS = {
  alt: 'alt',
  control: 'ctrl',
  ctrl: 'ctrl',
  meta: 'alt',
  opt: 'alt',
  option: 'alt',
  shift: 'shift'
} as const

/** `alt+shift+s`: alt and/or ctrl, optional shift, one letter or digit. Plain and shift-only chords are typing. */
export function parseChord(spec: string): Chord | null {
  const parts = spec
    .toLowerCase()
    .split('+')
    .map(p => p.trim())

  const key = parts.pop() ?? ''
  const mods = parts.map(p => MODIFIERS[p as keyof typeof MODIFIERS])
  const has = (m: string) => mods.includes(m as never)

  return /^[a-z0-9]$/.test(key) && !mods.includes(undefined as never) && (has('alt') || has('ctrl'))
    ? { alt: has('alt'), ctrl: has('ctrl'), key, shift: has('shift') }
    : null
}

const chordId = (c: Chord) => `${c.ctrl ? 'ctrl+' : ''}${c.alt ? 'alt+' : ''}${c.shift ? 'shift+' : ''}${c.key}`

/** Alt+Shift+S arrives as a capital S with shift set, or either alone, depending on the terminal's encoding. */
export const chordMatches = (c: Chord, key: { ctrl: boolean; meta: boolean; shift: boolean }, ch: string): boolean =>
  ch.toLowerCase() === c.key &&
  key.ctrl === c.ctrl &&
  key.meta === c.alt &&
  (key.shift || ch !== ch.toLowerCase()) === c.shift

/**
 * Chords the TUI itself owns, which a configured chord must not shadow: Alt+G / Ctrl+G (editor) and Ctrl+K
 * (dequeue) are handled after the chord table, and the composer's own editing (Alt+B/F/D word moves, Alt+V paste,
 * Ctrl+A/E/U/W/Z/Y) sees the key too, so a bound command would fire together with it. Keyed without shift.
 */
const RESERVED = new Set([
  'alt+b',
  'alt+d',
  'alt+f',
  'alt+g',
  'alt+v',
  ...'aeuwyz'.split('').map(k => `ctrl+${k}`),
  'ctrl+g',
  'ctrl+k'
])

export const isReservedChord = (c: Chord): boolean => RESERVED.has(chordId({ ...c, shift: false }))

/** The specs (in either layer) that name a reserved chord, so a line can say they are ignored. */
export const reservedSpecs = (
  ...layers: ReadonlyArray<Readonly<Record<string, unknown>> | null | undefined>
): string[] =>
  layers.flatMap(layer =>
    Object.keys(layer ?? {}).filter(spec => {
      const chord = parseChord(spec)

      return chord !== null && isReservedChord(chord)
    })
  )

/** The plugin defaults under the user's `display.tui_keybinds`; an empty or non-string command switches a chord off.
 *  Entries whose chord does not parse, or is reserved for the TUI itself, are dropped. */
export function resolveKeybinds(
  defaults: Readonly<Record<string, unknown>> = {},
  user: Readonly<Record<string, unknown>> = {}
): Keybind[] {
  const merged = new Map<string, Keybind | null>()

  for (const layer of [defaults, user]) {
    for (const [spec, value] of Object.entries(layer)) {
      const chord = parseChord(spec)
      const command = typeof value === 'string' ? value.trim().replace(/^\//, '') : ''

      if (chord && !isReservedChord(chord)) {
        merged.set(chordId(chord), command ? { chord, command } : null)
      }
    }
  }

  return [...merged.values()].filter((b): b is Keybind => b !== null)
}

// Reserved chords already announced (once per spec for the process).
const announcedReserved = new Set<string>()

/** One activity line per reserved spec found in the config, the first time it is seen. */
export function announceReserved(specs: readonly string[], say: (text: string) => void): void {
  specs
    .filter(spec => !announcedReserved.has(spec))
    .forEach(spec => {
      announcedReserved.add(spec)
      say(`keybind ${spec} ignored: reserved by the TUI`)
    })
}

export const resetAnnouncedReserved = (): void => announcedReserved.clear()

// The chords' defaults come with the slash catalog. When it never arrived (still loading, or the load failed)
// a press retries the load: at most MAX tries, RETRY_GAP_MS apart, one at a time.
const MAX_CATALOG_RETRIES = 3
const RETRY_GAP_MS = 2000
const catalogRetry = { at: 0, attempts: 0, busy: false }

export const resetCatalogRetry = (): void => void Object.assign(catalogRetry, { at: 0, attempts: 0, busy: false })

/** True when a retry was started. Never throws: a failing `load` only ends the attempt. */
export function retryCatalog(
  load: () => Promise<unknown>,
  say: (text: string) => void,
  now: number = Date.now()
): boolean {
  if (catalogRetry.busy || catalogRetry.attempts >= MAX_CATALOG_RETRIES || now - catalogRetry.at < RETRY_GAP_MS) {
    return false
  }

  catalogRetry.busy = true
  catalogRetry.attempts++
  catalogRetry.at = now
  say('keybinds not loaded, retrying')

  try {
    void Promise.resolve(load())
      .catch(() => undefined)
      .finally(() => void (catalogRetry.busy = false))
  } catch {
    catalogRetry.busy = false
  }

  return true
}

/** `display.tui_keybinds` as last read from the config; the plugin defaults ride the slash catalog. */
export const $configuredKeybinds = atom<Readonly<Record<string, unknown>>>({})

export const setConfiguredKeybinds = (value: unknown): void =>
  $configuredKeybinds.set(
    value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
  )

/** The whole composer: the lines already accepted with a trailing `\`, then the line being typed (what `submit` joins). */
export const composerDraftText = (inputBuf: readonly string[], input: string): string => [...inputBuf, input].join('\n')

/** What a directive may do: the composer edits it names, a line of output, and follow-up commands. */
export interface DirectiveHost {
  clear: () => void
  replace: (text: string) => void
  /** Runs a slash command on that session; rejects when the gateway does. */
  request: (command: string, sid: string, composerText?: string) => Promise<null | SlashExecResponse | undefined>
  say: (text: string) => void
}

const bare = (command: string) => command.replace(/^\//, '')

/**
 * Handle the answers to commands run on session `sid`. A directive that changes the composer applies only
 * while `intact()` holds (the session and the composer are what they were when the command started);
 * otherwise nothing is touched and a line says why (`why`). `done` runs after a picker row's command has answered.
 */
export function directives(
  host: DirectiveHost,
  sid: string,
  intact: () => boolean,
  done: () => void = () => {},
  why: () => string = () => 'the composer changed meanwhile'
) {
  const run = (command: string, composerText?: string): Promise<void> =>
    host
      .request(bare(command), sid, composerText)
      .then(r => apply(r))
      .catch((e: unknown) => host.say(`keybind /${bare(command)}: ${rpcErrorMessage(e)}`))

  const apply = (r: unknown): Promise<void> | void => {
    const d = parseCommandDispatch(r)

    if (!d) {
      const output = (r as null | SlashExecResponse | undefined)?.output

      return void (output && host.say(output))
    }

    if (d.type === 'exec' || d.type === 'plugin') {
      return void (d.output && host.say(d.output))
    }

    if ('notice' in d && d.notice?.trim()) {
      host.say(d.notice)
    }

    if (d.type === 'choices') {
      return showChoices(d.items, d.title, d.selected, command => void run(command).finally(done))
    }

    if (d.type !== 'prefill' && d.type !== 'composer-clear') {
      return
    }

    if (!intact()) {
      // A composer-clear answer means the command already ran (a save went through): only the clearing is skipped.
      return host.say(
        d.type === 'composer-clear' ? `keybind: saved, composer kept (${why()})` : `keybind: ${why()}, nothing applied`
      )
    }

    if (d.type === 'prefill') {
      host.replace(d.message)
    } else {
      host.clear()
    }

    // The follow-up (a store dropping the entry just restored) runs only once the composer really has it.
    return d.type === 'prefill' && d.ack
      ? host
          .request(bare(d.ack), sid)
          .then(a => void (a?.output && host.say(a.output)))
          .catch((e: unknown) => host.say(`keybind ${d.ack}: ${rpcErrorMessage(e)}`))
      : undefined
  }

  return { apply, run }
}

/** The handler for a command typed in the composer: no chord press to compare the composer with, so only `stale` guards it. */
export const typedDirectives = (
  gw: GatewayClient,
  setInput: (text: string) => void,
  say: (text: string) => void,
  sid: string,
  stale: () => boolean
) =>
  directives(
    {
      clear: () => setInput(''),
      replace: setInput,
      request: (command, session_id) => gw.request<SlashExecResponse>('slash.exec', { command, session_id }),
      say
    },
    sid,
    () => !stale()
  )

export interface KeybindHost extends DirectiveHost {
  composer: {
    attachments: () => boolean
    /** Moves on every edit of the composer, so a callback can tell the draft is still what it saw. */
    revision: () => number
    text: () => string
  }
  sid: () => null | string
}

// One run at a time per (session, command, text): a second press before the first answer would repeat it.
const running = new Set<string>()

/** Run `command` for a chord press, handing it the live composer text. `done` runs once everything it opened has answered. */
export function runKeybind(command: string, host: KeybindHost, done: () => void = () => {}): void {
  const { composer } = host
  const sid = host.sid()

  if (!sid) {
    return host.say('keybind: no active session')
  }

  // Text cannot carry an attachment: the command is told there is no composer text rather than a lossy one.
  const text = composer.attachments() ? undefined : composer.text()
  const revision = composer.revision()
  const key = [sid, command, text].join('\0')

  if (running.has(key)) {
    return
  }

  running.add(key)

  // A paste result landing does not move the revision (see useComposerState), so text that arrived while the
  // command ran is told apart by comparing the text itself with what the command was handed.
  const textKept = () => text === undefined || composer.text() === text

  const intact = () => host.sid() === sid && composer.revision() === revision && !composer.attachments() && textKept()

  const why = () =>
    host.sid() !== sid
      ? 'the session changed'
      : composer.attachments()
        ? 'an image was attached'
        : composer.revision() === revision && !textKept()
          ? 'text arrived meanwhile'
          : 'the composer changed meanwhile'

  void directives(host, sid, intact, done, why)
    .run(command, text)
    .finally(() => {
      running.delete(key)
      done()
    })
}

/**
 * The composer's side of the chords: `(key, ch) => true` when the press was a bound chord (and its command
 * started). `onSettled` runs once a command and anything it opened has answered.
 */
export const keybindRunner =
  ({ actions, composer, gateway }: InputHandlerContext, onSettled: () => void) =>
  (key: Key, ch: string): boolean => {
    const catalog = $slashCatalog.get()
    const modified = key.meta || key.ctrl
    const configured = $configuredKeybinds.get()

    if (modified) {
      announceReserved(reservedSpecs(catalog?.keybinds, configured), text => turnController.pushActivity(text, 'info'))
    }

    const bound = modified && resolveKeybinds(catalog?.keybinds, configured).find(b => chordMatches(b.chord, key, ch))

    // No catalog means no plugin defaults (Alt+S): try loading it again rather than do nothing.
    if (!bound && modified && !catalog && /^[a-z0-9]$/i.test(ch)) {
      retryCatalog(
        () => loadSlashCatalog(gateway.rpc, getUiState().sid, setSlashCatalog),
        text => turnController.pushActivity(text, 'info')
      )
    }

    if (bound) {
      runKeybind(
        bound.command,
        {
          clear: composer.actions.clearIn,
          composer: {
            attachments: () => composer.refs.tokensRef.current.length > 0,
            revision: () => composer.refs.revisionRef.current,
            text: () => composerDraftText(composer.state.inputBuf, composer.refs.inputRef.current)
          },
          replace: composer.actions.setInput,
          request: (command, session_id, composer_text) =>
            gateway.gw.request<SlashExecResponse>('slash.exec', {
              command,
              session_id,
              ...(composer_text === undefined ? {} : { composer_text })
            }),
          say: actions.sys,
          sid: () => getUiState().sid
        },
        onSettled
      )
    }

    return Boolean(bound)
  }
