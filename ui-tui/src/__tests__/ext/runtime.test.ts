import { describe, expect, it, vi } from 'vitest'

import type { ExtensionListing, HookSpec } from '../../sdk/ext/contract.js'
import { createRuntime } from '../../sdk/ext/runtime.js'
import { memoryStore } from '../../sdk/ext/store.js'

const LISTING: ExtensionListing = {
  entry: '/p/tui/a.mjs',
  granted: true,
  id: 'p/a',
  name: 'a',
  plugin: 'p',
  reason: '',
  rpc: [],
  root: '/p',
  sdk: 1,
  sha256: 'a'.repeat(64),
  title: 'a'
}

const HOOKS: Record<string, HookSpec> = {
  'x.all': { mode: 'all' },
  'x.first': { mode: 'first' },
  'x.transform': { mode: 'transform' }
}

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

const setup = (extra: Partial<Parameters<typeof createRuntime>[0]> = {}) => {
  const notices: string[] = []
  const store = memoryStore()
  const runtime = createRuntime({ hooks: HOOKS, notify: text => void notices.push(text), store, ...extra })
  const entry = runtime.track(LISTING, 'active', '')

  return { entry, notices, runtime, store }
}

describe('checkSdk', () => {
  const shim = (api: object) => ({ ...api, legacy: true }) as never

  it('accepts the current SDK and refuses invalid values', () => {
    const { runtime } = setup({ minSdk: 1, sdk: 2, shims: { 1: shim } })

    expect(runtime.checkSdk(2)).toEqual({ ok: true, shim: null })
    expect(runtime.checkSdk(0)).toMatchObject({ detail: 'declares an invalid sdkVersion (0)', ok: false })
    expect(runtime.checkSdk(1.5)).toMatchObject({ ok: false })
    expect(runtime.checkSdk('1')).toMatchObject({ detail: 'declares an invalid sdkVersion (1)', ok: false })
  })

  it('refuses an extension that needs a newer SDK, with the way out', () => {
    const { runtime } = setup({ sdk: 2 })

    expect(runtime.checkSdk(3)).toEqual({
      detail: 'needs SDK 3, this TUI has SDK 2: update the TUI (nix flake update hermes, rebuild)',
      ok: false
    })
  })

  it('runs an older extension through its shim, and refuses one older than the minimum or without a shim', () => {
    const withShim = setup({ minSdk: 1, sdk: 2, shims: { 1: shim } }).runtime
    const noShim = setup({ minSdk: 1, sdk: 2 }).runtime
    const tooOld = setup({ minSdk: 2, sdk: 2 }).runtime

    expect(withShim.checkSdk(1)).toMatchObject({ ok: true })
    expect((withShim.checkSdk(1) as { shim: unknown }).shim).toBe(shim)
    expect(noShim.checkSdk(1)).toEqual({
      detail: 'targets SDK 1 and this TUI (SDK 2) has no compat shim for it: update the extension',
      ok: false
    })
    expect(tooOld.checkSdk(1)).toEqual({
      detail: 'targets SDK 1, this TUI supports SDK 2..2: update the extension',
      ok: false
    })
  })
})

describe('guard', () => {
  it('returns the value of a healthy call and records no failure', async () => {
    const { entry, runtime } = setup()

    expect(await runtime.guard(entry, 't', () => 7)).toEqual({ ok: true, value: 7 })
    expect(await runtime.guard(entry, 't', async () => 8)).toEqual({ ok: true, value: 8 })
    expect(entry.failures).toBe(0)
  })

  it('turns a throw, a rejection and a timeout into failures, with one notice for the first', async () => {
    const { entry, notices, runtime } = setup({ callTimeoutMs: 30, maxFailures: 10 })

    expect(
      await runtime.guard(entry, 'a', () => {
        throw new Error('boom')
      })
    ).toEqual({ ok: false })
    expect(await runtime.guard(entry, 'b', () => Promise.reject(new Error('nope')))).toEqual({ ok: false })
    expect(await runtime.guard(entry, 'c', () => new Promise(() => {}))).toEqual({ ok: false })
    expect(entry.failures).toBe(3)
    expect(entry.errors.map(line => line.replace(/^\S+ /, ''))).toEqual([
      'a: boom',
      'b: nope',
      'c: timed out after 30ms'
    ])
    expect(notices).toEqual(['extension p/a: a failed: boom (/extensions errors p/a)'])
  })

  it('counts a synchronous part longer than slowCallMs but keeps the value', async () => {
    const { entry, runtime } = setup({ maxFailures: 10, slowCallMs: 20 })

    const result = await runtime.guard(entry, 'slow', () => {
      const end = Date.now() + 60

      while (Date.now() < end) {
        // busy
      }

      return 'late'
    })

    expect(result).toEqual({ ok: true, value: 'late' })
    expect(entry.failures).toBe(1)
    expect(entry.errors[0]).toMatch(/slow: blocked the UI for \d+ms \(limit 20ms\)/)
  })

  it('disables after maxFailures, persists the reason and disposes what it registered', async () => {
    const { entry, notices, runtime, store } = setup({ maxFailures: 3 })
    const dispose = vi.fn()

    entry.disposers.push(dispose)
    entry.hooks.set('x.first', [() => 1])

    for (let i = 0; i < 3; i++) {
      await runtime.guard(entry, 'h', () => {
        throw new Error('bad')
      })
    }

    expect(entry.status).toBe('disabled')
    expect(entry.detail).toBe('3 failures, last: h: bad')
    expect(store.read()).toEqual({ 'p/a': '3 failures, last: h: bad' })
    expect(dispose).toHaveBeenCalledTimes(1)
    expect(entry.hooks.size).toBe(0)
    expect(notices).toEqual([
      'extension p/a: h failed: bad (/extensions errors p/a)',
      'extension p/a disabled: 3 failures, last: h: bad (re-enable with /extensions enable p/a)'
    ])
  })

  it('a rejection that arrives after the timeout won is not an unhandled rejection', async () => {
    const { entry, runtime } = setup({ callTimeoutMs: 10, maxFailures: 10 })
    const seen = vi.fn()

    process.once('unhandledRejection', seen)
    await runtime.guard(entry, 'late', () => new Promise((_, reject) => setTimeout(() => reject(new Error('x')), 40)))
    await sleep(80)
    process.off('unhandledRejection', seen)

    expect(seen).not.toHaveBeenCalled()
  })

  it('tells the watchdog around the synchronous part only', async () => {
    const calls: string[] = []
    const { entry, runtime } = setup({
      watchdog: { enter: id => void calls.push(`enter ${id}`), leave: () => void calls.push('leave') }
    })

    await runtime.guard(entry, 'w', () => void calls.push('run'))

    expect(calls).toEqual(['enter p/a', 'run', 'leave'])
  })
})

describe('hook modes', () => {
  const register = (runtime: ReturnType<typeof setup>['runtime'], id: string, name: string, fn: (p: any) => any) => {
    const entry = runtime.track({ ...LISTING, id }, 'active', '')

    entry.hooks.set(name, [fn])

    return entry
  }

  it('transform: merges returned objects in order, undefined keeps, null cancels', async () => {
    const { runtime } = setup()

    runtime.untrack('p/a')
    register(runtime, 'a/1', 'x.transform', p => ({ text: `${p.text}1` }))
    register(runtime, 'a/2', 'x.transform', () => undefined)
    register(runtime, 'a/3', 'x.transform', p => ({ text: `${p.text}3` }))

    expect(await runtime.run('x.transform', { bracketed: true, text: 'a' })).toEqual({ bracketed: true, text: 'a13' })

    register(runtime, 'a/4', 'x.transform', () => null)
    register(runtime, 'a/5', 'x.transform', () => {
      throw new Error('never reached after a cancel')
    })

    expect(await runtime.run('x.transform', { bracketed: true, text: 'a' })).toBeNull()
  })

  it('first: the first non-null result wins; a failing handler is skipped; no result is null', async () => {
    const { runtime } = setup({ maxFailures: 10 })

    runtime.untrack('p/a')
    register(runtime, 'a/1', 'x.first', () => null)
    register(runtime, 'a/2', 'x.first', () => {
      throw new Error('x')
    })
    register(runtime, 'a/3', 'x.first', async () => '/tmp/clip.png')
    register(runtime, 'a/4', 'x.first', () => '/tmp/other.png')

    expect(await runtime.run('x.first', {})).toBe('/tmp/clip.png')
    runtime.untrack('a/3')
    runtime.untrack('a/4')
    expect(await runtime.run('x.first', {})).toBeNull()
  })

  it('all: every active handler runs; disabled and unknown hooks do nothing', async () => {
    const { runtime } = setup()
    const seen: string[] = []

    runtime.untrack('p/a')
    register(runtime, 'a/1', 'x.all', () => void seen.push('1'))
    const off = register(runtime, 'a/2', 'x.all', () => void seen.push('2'))

    runtime.disable(off, 'test', { silent: true })
    expect(await runtime.run('x.all', {})).toBeUndefined()
    expect(seen).toEqual(['1'])
    expect(await runtime.run('no.such.hook', 'payload')).toBe('payload')
    expect(runtime.hasHook('x.all')).toBe(true)
    expect(runtime.hasHook('x.first')).toBe(false)
  })
})

describe('activate', () => {
  it('marks an extension whose activate throws as failed and removes what it registered', async () => {
    const { runtime } = setup()
    const entry = runtime.track({ ...LISTING, id: 'p/b' }, 'loading', '')
    const dispose = vi.fn()

    await runtime.activate(
      entry,
      {
        activate: () => {
          entry.disposers.push(dispose)
          entry.hooks.set('x.all', [() => 1])
          throw new Error('half done')
        },
        sdkVersion: 1
      },
      () => ({}) as never
    )

    expect(entry.status).toBe('failed')
    expect(entry.detail).toMatch(/activate: half done$/)
    expect(entry.hooks.size).toBe(0)
    expect(dispose).toHaveBeenCalledTimes(1)
  })

  it('times a hanging async activate out', async () => {
    const { runtime } = setup({ activateTimeoutMs: 30 })
    const entry = runtime.track({ ...LISTING, id: 'p/c' }, 'loading', '')

    await runtime.activate(entry, { activate: () => new Promise(() => {}), sdkVersion: 1 }, () => ({}) as never)

    expect(entry.status).toBe('failed')
    expect(entry.detail).toMatch(/timed out after 30ms$/)
  })
})
