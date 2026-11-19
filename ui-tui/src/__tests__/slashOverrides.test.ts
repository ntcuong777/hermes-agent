import { afterEach, expect, it } from 'vitest'

import { findSlashCommand } from '../app/slash/registry.js'
import type { SlashCommand } from '../app/slash/types.js'
import { registerSlashOverride } from '../sdk/ext/slashOverrides.js'

const stockBg = findSlashCommand('bg')
const stockCompress = findSlashCommand('compress')

const command = (name: string, aliases?: string[]): SlashCommand => ({ aliases, help: 'x', name, run: () => {} })

afterEach(() => {
  // A later registration wins, so restoring the upstream entries is a re-registration of them.
  registerSlashOverride(stockBg!)
  registerSlashOverride(stockCompress!)
})

it('answers with the built-in command until an extension overrides it', () => {
  expect(findSlashCommand('bg')).toBe(stockBg)
  expect(stockBg?.name).toBe('bg')
})

it('serves an override under its name and its aliases, case-insensitively', () => {
  const override = command('bg', ['background'])

  registerSlashOverride(override)

  expect(findSlashCommand('bg')).toBe(override)
  expect(findSlashCommand('Background')).toBe(override)
  expect(findSlashCommand('btw')).not.toBe(override)
})

it('lets the later registration of a name win', () => {
  const first = command('compress')
  const second = command('compress')

  registerSlashOverride(first)
  registerSlashOverride(second)

  expect(findSlashCommand('compress')).toBe(second)
})

it('exposes the registration seam on globalThis for extension bundles', () => {
  expect(typeof (globalThis as { __hermesRegisterSlashOverride?: unknown }).__hermesRegisterSlashOverride).toBe(
    'function'
  )
})
