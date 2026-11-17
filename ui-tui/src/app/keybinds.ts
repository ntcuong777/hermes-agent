import type { Key } from '@hermes/ink'
import type { SlashExecResponse } from '@hermes/shared/gateway-types'
import { parseCommandDispatch } from '@hermes/shared/slash'
import { atom } from 'nanostores'

import type { GatewayClient } from '../gatewayClient.js'
import { rpcErrorMessage } from '../lib/rpc.js'

import { showChoices } from './choicePicker.js'
import type { InputHandlerContext } from './interfaces.js'
import { $slashCatalog } from './slashCatalog.js'
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

/** The plugin defaults under the user's `display.tui_keybinds`; an empty or non-string command switches a chord off.
 *  Entries whose chord does not parse are dropped. */
export function resolveKeybinds(
  defaults: Readonly<Record<string, unknown>> = {},
  user: Readonly<Record<string, unknown>> = {}
): Keybind[] {
  const merged = new Map<string, Keybind | null>()

  for (const layer of [defaults, user]) {
    for (const [spec, value] of Object.entries(layer)) {
      const chord = parseChord(spec)
      const command = typeof value === 'string' ? value.trim().replace(/^\//, '') : ''

      if (chord) {
        merged.set(chordId(chord), command ? { chord, command } : null)
      }
    }
  }

  return [...merged.values()].filter((b): b is Keybind => b !== null)
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
 * otherwise nothing is touched and a line says so. `done` runs after a picker row's command has answered.
 */
export function directives(host: DirectiveHost, sid: string, intact: () => boolean, done: () => void = () => {}) {
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
      return host.say('keybind: the composer changed meanwhile, nothing applied')
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

  const intact = () => host.sid() === sid && composer.revision() === revision && !composer.attachments()

  void directives(host, sid, intact, done)
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
    const bound =
      (key.meta || key.ctrl) &&
      resolveKeybinds($slashCatalog.get()?.keybinds, $configuredKeybinds.get()).find(b =>
        chordMatches(b.chord, key, ch)
      )

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
