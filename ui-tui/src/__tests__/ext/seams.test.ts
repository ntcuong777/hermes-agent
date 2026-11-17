/**
 * The host adapters that live in upstream-owned files. An upstream sync that drops one of these lines
 * fails here, with the file named, instead of silently turning the extension host off.
 */
import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const source = (path: string) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8')

describe('upstream-file seams of the extension host', () => {
  it('app/slash/registry.ts registers the extension commands', () => {
    const text = source('app/slash/registry.ts')

    expect(text).toContain("import { extensionCommands } from '../../sdk/ext/slash.js'")
    expect(text).toMatch(/^\s+\.\.\.extensionCommands,/m)
  })

  it('entry.tsx mounts the cached slot extensions before the first frame', () => {
    const text = source('entry.tsx')
    const early = text.indexOf("await (await import('./sdk/ext/earlyLoad.js')).primeExtensionSlots(gw)")

    expect(early).toBeGreaterThan(0)
    expect(early).toBeLessThan(text.indexOf('ink.render(<App gw={gw} />'))
  })

  it('scripts/build/tui.mjs embeds the upstream region hashes of the slots', () => {
    const text = source('../../scripts/build/tui.mjs')

    expect(text).toContain("import { slotHashesPlugin } from '../../ui-tui/src/sdk/ext/slotHashes.mjs'")
    expect(text).toContain('plugins: [stubDevtools, slotHashesPlugin(source)]')
  })

  it('app/useMainApp.ts attaches the host to the gateway', () => {
    const text = source('app/useMainApp.ts')

    expect(text).toContain("import { useExtensionHost } from '../sdk/ext/useExtensionHost.js'")
    expect(text).toMatch(/^\s+useExtensionHost\(gw, sys\)/m)
  })
})
