/**
 * The whole slot pipeline on REAL code: the actual ApprovalPrompt of components/prompts.tsx is copied (the file text
 * as it is), built by tui-slot-build, mounted through the fake host's slot registry and rendered next to the stock
 * component. Same props and keystrokes must write the same bytes. This is the proof every stage repeats for its slot.
 */
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

import * as ink from '@hermes/ink'
import * as esbuild from 'esbuild'
import * as React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// @ts-expect-error plain JS, no types
import { buildSlotSource, moduleId } from '../../../../scripts/fork/tui-slot-build.mjs'
import * as overlayPrimitives from '../../components/overlayPrimitives.js'
import { ApprovalPrompt } from '../../components/prompts.js'
import * as textInput from '../../components/textInput.js'
import * as i18nRuntime from '../../i18n/runtime.js'
import * as useT from '../../i18n/useT.js'
import * as libText from '../../lib/text.js'
import { sameRender } from '../../sdk/ext/frames.js'
import { regionHash } from '../../sdk/ext/slotRegion.mjs'
import { slotComponent } from '../../sdk/ext/slotReact.js'
import { createFakeHost } from '../../sdk/ext/testing.js'
import { DEFAULT_THEME } from '../../theme.js'

const PROMPTS = new URL('../../components/prompts.tsx', import.meta.url)

const HOST = {
  'components/overlayPrimitives': overlayPrimitives,
  'components/textInput': textInput,
  'i18n/runtime': i18nRuntime,
  'i18n/useT': useT,
  ink,
  'lib/text': libText,
  react: React
}

const SLOTS = {
  'test.approval': {
    file: 'ui-tui/src/components/prompts.tsx',
    host: Object.keys(HOST),
    kind: 'component' as const,
    props: 1,
    summary: 't',
    symbols: ['ApprovalPrompt']
  }
}

const req = {
  command: `rm -rf build\nls\n${'x'.repeat(40)}`,
  description: 'delete the build dir',
  pattern_key: 'rm'
} as never

const props = { cols: 70, onChoice: () => {}, onTell: () => {}, req, t: DEFAULT_THEME }

/**
 * Build the copy of the whole prompts.tsx (every top-level export of it), activate it in a fake host and return the host.
 * `edit` changes the copy (a deliberate fork change); `embedded` is the hash "this TUI build" carries for upstream.
 */
const mounted = async (
  edit: (text: string) => string = text => text,
  embedded?: string,
  onDrift?: 'apply' | 'skip'
) => {
  const upstreamText = readFileSync(PROMPTS, 'utf8')
  const recorded = regionHash(upstreamText, ['ApprovalPrompt'])

  const copy = `${edit(upstreamText)}\nexport const sdkVersion = 1\nexport const slots = { 'test.approval': ApprovalPrompt }\n`
  const built = await buildSlotSource(copy, 'ui-tui/src/components', esbuild)
  const file = join(mkdtempSync(join(tmpdir(), 'slot-pipe-')), 'approval.mjs')

  writeFileSync(file, built.code)

  const host = createFakeHost({ host: HOST, slotDefs: SLOTS, upstream: { 'test.approval': embedded ?? recorded } })
  const module = (await import(/* @vite-ignore */ pathToFileURL(file).href)) as { default: never }

  const entry = await host.activate(module.default, {
    slots: [
      {
        expectUpstream: recorded,
        id: 'test.approval',
        onDrift,
        props: 1,
        reason: 'pipeline test',
        upstreamCommit: 'abcdef0123456789'
      }
    ]
  })

  return { built, entry, host }
}

describe('slot pipeline on the real ApprovalPrompt', () => {
  beforeEach(() => void vi.spyOn(process.stdout, 'write').mockImplementation(() => true))

  it('resolves import specifiers to host module ids', () => {
    expect(moduleId('../app/overlayStore.js', 'ui-tui/src/components')).toBe('app/overlayStore')
    expect(moduleId('./textInput.js', 'ui-tui/src/components')).toBe('components/textInput')
    expect(moduleId('@hermes/ink', 'ui-tui/src/components')).toBe('ink')
    expect(moduleId('@hermes/shared/fuzzy', 'ui-tui/src/components')).toBe('shared/fuzzy')
    expect(moduleId('@nanostores/react', 'ui-tui/src/components')).toBe('pkg/@nanostores/react')
  })

  it('builds, activates and mounts the copy; the log line names the slot, the extension and the upstream commit', async () => {
    const { built, entry, host } = await mounted()

    expect(built.ids).toEqual(Object.keys(HOST).sort())
    expect(entry.status).toBe('active')
    expect(host.runtime.slotRows()[0]).toMatchObject({
      id: 'test.approval',
      status: 'active',
      upstreamCommit: 'abcdef0123456789'
    })
    expect(host.logs.some(line => line.includes('tui slot test.approval active') && line.includes('abcdef0123'))).toBe(
      true
    )
    expect(host.slotEvents.map(event => event.kind)).toEqual(['applied'])
  })

  it('the mounted copy writes the same bytes as the stock component, also after keys', async () => {
    const { host } = await mounted()
    const Slot = slotComponent('test.approval', ApprovalPrompt, host.host)

    const idle = await sameRender(<ApprovalPrompt {...props} />, <Slot {...props} />)
    const keyed = await sameRender(<ApprovalPrompt {...props} />, <Slot {...props} />, { keys: ['v', '\u001b[B'] })

    expect(idle.textB).toContain('delete the build dir')
    expect(host.runtime.slotFor('test.approval')?.extId).toBe('fake/ext')
    expect(idle.same).toBe(true)
    expect(keyed.same).toBe(true)
  })

  it('a deliberately changed copy writes different bytes, so the equality above is not vacuous', async () => {
    const { host } = await mounted(text => text.replace('Esc/Ctrl+C deny', 'Esc/Ctrl+C DENY'))
    const Slot = slotComponent('test.approval', ApprovalPrompt, host.host)
    const result = await sameRender(<ApprovalPrompt {...props} />, <Slot {...props} />)

    expect(result.same).toBe(false)
    expect(result.textB).toContain('DENY')
    expect(result.textA).not.toContain('DENY')
  })

  it('upstream drift with on_drift apply (the default): the copy is mounted and renders, the drift is reported', async () => {
    const { host } = await mounted(text => text, 'f'.repeat(64))
    const Slot = slotComponent('test.approval', ApprovalPrompt, host.host)

    expect(host.runtime.slotRows()[0]).toMatchObject({ drifted: true, status: 'active' })
    expect(host.runtime.slotFor('test.approval')?.extId).toBe('fake/ext')
    expect(host.slotEvents.map(event => event.kind)).toEqual(['drift-first-seen'])
    expect((await sameRender(<ApprovalPrompt {...props} />, <Slot {...props} />)).same).toBe(true)
  })

  it('upstream drift with on_drift skip (the build embeds another hash): not mounted, the stock component renders, loudly', async () => {
    const { host } = await mounted(text => text, 'f'.repeat(64), 'skip')
    const Slot = slotComponent('test.approval', ApprovalPrompt, host.host)
    const row = host.runtime.slotRows()[0]

    expect(row?.status).toBe('skipped-drift')
    expect(row?.detail).toContain('upstream ui-tui/src/components/prompts.tsx changed since abcdef0123')
    expect(host.runtime.slotFor('test.approval')).toBeUndefined()
    expect(host.notices.some(n => n.level === 'warn' && n.text.includes("upstream's own behaviour runs"))).toBe(true)
    expect(host.slotEvents.map(event => event.kind)).toEqual(['drift-first-seen'])
    expect((await sameRender(<ApprovalPrompt {...props} />, <Slot {...props} />)).same).toBe(true)
  })
})
