/** Persistence of the "do not load" list: `$HERMES_HOME/tui-extensions.json` = `{ "disabled": { "<id>": "<reason>" } }`. */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

import type { DisabledStore } from './contract.js'

/**
 * Where the host keeps its files. Under vitest (and no explicit HERMES_HOME) it is a temp directory, so a test that
 * attaches a host with default paths can never write into the person's real `~/.hermes`.
 */
export const hermesHome = (env: NodeJS.ProcessEnv = process.env): string =>
  env.HERMES_HOME?.trim() || (env.VITEST ? join(tmpdir(), 'hermes-vitest-home') : join(homedir(), '.hermes'))

export const defaultStatePath = (env: NodeJS.ProcessEnv = process.env): string =>
  join(hermesHome(env), 'tui-extensions.json')

const parse = (text: string): Record<string, string> => {
  try {
    const data = JSON.parse(text) as { disabled?: unknown }
    const disabled = data && typeof data === 'object' ? data.disabled : null

    return disabled && typeof disabled === 'object' && !Array.isArray(disabled)
      ? Object.fromEntries(Object.entries(disabled).map(([id, reason]) => [id, String(reason)]))
      : {}
  } catch {
    return {}
  }
}

/** A missing or unreadable file reads as "nothing disabled"; a write that fails is swallowed (the in-memory state still holds). */
export const fileStore = (path: string = defaultStatePath()): DisabledStore => ({
  read: () => {
    try {
      return parse(readFileSync(path, 'utf8'))
    } catch {
      return {}
    }
  },
  write: next => {
    try {
      mkdirSync(dirname(path), { recursive: true })
      writeFileSync(`${path}.tmp`, `${JSON.stringify({ disabled: next }, null, 2)}\n`)
      renameSync(`${path}.tmp`, path)
    } catch {
      // read-only home: the extension stays disabled for this run only
    }
  }
})

export const memoryStore = (initial: Record<string, string> = {}): DisabledStore => {
  let held = { ...initial }

  return { read: () => ({ ...held }), write: next => void (held = { ...next }) }
}
