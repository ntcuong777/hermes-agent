import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import * as esbuild from 'esbuild'
import { describe, expect, it } from 'vitest'

// @ts-expect-error plain JS, no types
import { buildSlotSource, rewriteImports } from '../../../../scripts/fork/tui-slot-build.mjs'
import { slotHashesPlugin, slotHashTable } from '../../sdk/ext/slotHashes.mjs'
import { regionHash } from '../../sdk/ext/slotRegion.mjs'

const DIR = 'ui-tui/src/components'

describe('rewriteImports', () => {
  it('turns named, default, namespace and multi-line imports into host lookups and drops type imports', () => {
    const { code, ids } = rewriteImports(
      [
        "import { Box, type Key, Text as T } from '@hermes/ink'",
        "import React, { useState } from 'react'",
        "import * as store from '../app/overlayStore.js'",
        'import {',
        '  a,',
        '  b',
        "} from './helpers.js'",
        "import type { Theme } from '../theme.js'",
        "import { readFileSync } from 'node:fs'",
        'const x = 1'
      ].join('\n'),
      DIR
    )

    expect(code).toBe(
      [
        "const { Box, Text: T } = __host('ink')",
        "const React = __host('react').default ?? __host('react')\nconst { useState } = __host('react')",
        "const store = __host('app/overlayStore')",
        "const { a, b } = __host('components/helpers')",
        '',
        "import { readFileSync } from 'node:fs'",
        'const x = 1'
      ].join('\n')
    )
    expect(ids).toEqual(['app/overlayStore', 'components/helpers', 'ink', 'react'])
  })

  it('rewrites an import that carries a trailing comment (a fork seam marker), not spanning into the next import', () => {
    const { code, ids } = rewriteImports(
      [
        "import { extensionHost } from '../sdk/ext/host.js' // fork: tui extension host",
        "import { transformPasteText } from '../sdk/ext/composerHooks.js' // fork: tui extension host",
        'const x = 1'
      ].join('\n'),
      DIR
    )

    expect(code).toBe(
      [
        "const { extensionHost } = __host('sdk/ext/host')",
        "const { transformPasteText } = __host('sdk/ext/composerHooks')",
        'const x = 1'
      ].join('\n')
    )
    expect(ids).toEqual(['sdk/ext/composerHooks', 'sdk/ext/host'])
  })

  it('refuses a side-effect import', () => {
    expect(() => rewriteImports("import './x.js'\n", DIR)).toThrow('side-effect import')
  })
})

describe('buildSlotSource', () => {
  const copy =
    "import { Text } from '@hermes/ink'\nexport const Hello = ({ n }: { n: number }) => <Text>hi {n}</Text>\nexport const sdkVersion = 1\nexport const slots = { 't.c': Hello }\n"

  it("a .ts copy (loader 'ts') compiles a TS generic arrow that the tsx loader would read as JSX", async () => {
    const source = 'const journaled = <T>(write: () => T): { value: T } | undefined => {\n  return { value: write() }\n}\nexport const useIt = () => journaled\nexport const sdkVersion = 1\nexport const slots = { submission: useIt }\n'
    const { code } = await buildSlotSource(source, DIR, esbuild, 'ts')

    // The ts loader ERASES the type parameter; the tsx loader would have died on `<T>` as JSX.
    expect(code).toContain('const journaled = (write) => {')
    expect(code).toContain('export default {')
  })

  it('compiles JSX to the host createElement and ends with a default export that registers the slots', async () => {
    const { code, ids } = await buildSlotSource(copy, DIR, esbuild)

    expect(ids).toEqual(['ink'])
    expect(code).toContain("const __h = __host('react').createElement")
    expect(code).toContain('__h(Text')
    expect(code).toContain('api.slots.register(id, impl)')
    expect(code).not.toContain('import {')
  })

  it('is deterministic', async () => {
    expect((await buildSlotSource(copy, DIR, esbuild)).code).toBe((await buildSlotSource(copy, DIR, esbuild)).code)
  })

  it('refuses a copy without sdkVersion and slots exports', async () => {
    await expect(buildSlotSource('export const a = 1\n', DIR, esbuild)).rejects.toThrow(
      'must export sdkVersion and slots'
    )
  })

  it('the built module throws a message naming a host module this TUI does not expose', async () => {
    const { code } = await buildSlotSource(
      "import { zzz } from '../lib/nothere.js'\nexport const sdkVersion = 1\nexport const slots = {}\n",
      DIR,
      esbuild
    )

    const dataUrl = `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`

    Object.defineProperty(globalThis, Symbol.for('hermes.tui.ext.host'), { configurable: true, value: { react: {} } })
    await expect(import(/* @vite-ignore */ dataUrl)).rejects.toThrow(
      "slot copy needs host module 'lib/nothere' (this TUI exposes: react)"
    )
  })
})

describe('slotHashes (the build-time table)', () => {
  const FILE = 'export const a = 1\n\nexport const b = 2\n'

  const repo = () => {
    const root = mkdtempSync(join(tmpdir(), 'slot-hashes-'))

    mkdirSync(join(root, 'ui-tui/src/sdk/ext'), { recursive: true })
    writeFileSync(join(root, 'ui-tui/src/a.ts'), FILE)
    writeFileSync(
      join(root, 'ui-tui/src/sdk/ext/slots.json'),
      JSON.stringify({
        slots: {
          gone: { file: 'ui-tui/src/none.ts', symbols: ['x'] },
          nosym: { file: 'ui-tui/src/a.ts', symbols: ['zz'] },
          ok: { file: 'ui-tui/src/a.ts', symbols: ['b'] }
        }
      })
    )

    return root
  }

  it('hashes the region of every readable slot and leaves out the rest', () => {
    const root = repo()

    expect(slotHashTable(root, join(root, 'ui-tui/src/sdk/ext/slots.json'))).toEqual({ ok: regionHash(FILE, ['b']) })
  })

  it('the esbuild plugin replaces upstreamHashes.ts with that table', () => {
    const root = repo()
    let handler: ((args: { path: string }) => { contents: string; loader: string }) | undefined

    slotHashesPlugin(root).setup({ onLoad: (_filter: unknown, fn: typeof handler) => void (handler = fn) } as never)

    const out = handler!({ path: join(root, 'ui-tui/src/sdk/ext/upstreamHashes.ts') })

    expect(out.loader).toBe('ts')
    expect(out.contents).toContain(`"ok":"${regionHash(FILE, ['b'])}"`)
    expect(out.contents).not.toContain('gone')
  })
})
