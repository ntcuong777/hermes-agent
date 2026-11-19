import { describe, expect, it } from 'vitest'

import { DENIED_RPC, rpcAllowed } from '../../sdk/ext/api.js'
import type { ExtensionModule } from '../../sdk/ext/contract.js'
import { createFakeHost } from '../../sdk/ext/testing.js'

const HOOKS = { 't.transform': { mode: 'transform' as const } }

const ext = (activate: ExtensionModule['activate']): ExtensionModule => ({ activate, sdkVersion: 1 })

describe('rpcAllowed', () => {
  it('always allows the own plugin namespace, nothing else unless declared', () => {
    expect(rpcAllowed('tui-ports', [], 'plugin.tui-ports.list')).toBe(true)
    expect(rpcAllowed('tui-ports', [], 'plugin.other.list')).toBe(false)
    expect(rpcAllowed('tui-ports', [], 'session.list')).toBe(false)
    expect(rpcAllowed('tui-ports', ['session.list'], 'session.list')).toBe(true)
    expect(rpcAllowed('tui-ports', ['session.*'], 'session.active_list')).toBe(true)
    expect(rpcAllowed('tui-ports', ['session.*'], 'sessions.list')).toBe(false)
  })

  it('never allows the denied methods, even when declared', () => {
    expect([...DENIED_RPC].sort()).toEqual(['config.set', 'shell.exec'])
    expect(rpcAllowed('p', ['shell.exec', 'config.*'], 'shell.exec')).toBe(false)
    expect(rpcAllowed('p', ['config.set'], 'config.set')).toBe(false)
  })
})

describe('the api an extension receives', () => {
  it('is frozen and carries id, plugin and the declared sdk version', async () => {
    const host = createFakeHost()
    let seen: any

    await host.activate(
      ext(api => void (seen = api)),
      { name: 'one', plugin: 'demo' }
    )

    expect(Object.isFrozen(seen)).toBe(true)
    expect(seen).toMatchObject({ id: 'demo/one', plugin: 'demo', sdkVersion: 1 })
    expect(() => {
      seen.id = 'other'
    }).toThrow()
  })

  it('rpc goes through the allowlist and the gateway; denied calls reject with the way out', async () => {
    const host = createFakeHost({ rpc: { 'plugin.demo.ping': p => ({ pong: p.n }) } })
    const results: unknown[] = []

    const entry = await host.activate(
      ext(async api => {
        results.push(await api.rpc('plugin.demo.ping', { n: 3 }))
        results.push(await api.rpc('session.list').catch((e: Error) => e.message))
        results.push(await api.rpc('shell.exec').catch((e: Error) => e.message))
      }),
      { plugin: 'demo', rpc: ['shell.exec'] }
    )

    expect(entry.status).toBe('active')
    expect(results).toEqual([
      { pong: 3 },
      'rpc session.list is not allowed for demo/ext: declare it under tui_extensions[].rpc in plugin.yaml',
      'rpc shell.exec is not allowed for demo/ext: declare it under tui_extensions[].rpc in plugin.yaml'
    ])
    expect(host.rpcCalls).toEqual([{ method: 'plugin.demo.ping', params: { n: 3 } }])
  })

  it('state is a frozen snapshot with change notifications, and unsubscribe works', async () => {
    const host = createFakeHost({ state: { sid: 'a' } })
    const seen: string[] = []

    await host.activate(
      ext(api => {
        const off = api.state.subscribe(s => void seen.push(String(s.sid)))

        seen.push(`now ${String(api.state.get().sid)}`)
        api.onDispose(off)
        expect(Object.isFrozen(api.state.get())).toBe(true)
      })
    )

    host.setState({ sid: 'b' })
    expect(seen).toEqual(['now a', 'b'])
    host.runtime.disable(host.runtime.get('fake/ext')!, 'done', { silent: true })
    host.setState({ sid: 'c' })
    expect(seen).toEqual(['now a', 'b'])
  })

  it('log and notify are tagged with the extension id', async () => {
    const host = createFakeHost()

    await host.activate(
      ext(api => {
        api.log('hello log')
        api.notify('hello notice', 'warn')
      }),
      { name: 'x', plugin: 'y' }
    )

    expect(host.logs).toEqual(['[ext y/x] hello log'])
    expect(host.notices).toEqual([{ level: 'warn', text: '[y/x] hello notice' }])
  })

  it('an unknown hook name fails the activation with the list of known ones', async () => {
    const host = createFakeHost({ hooks: HOOKS })
    const entry = await host.activate(ext(api => void api.hooks.on('nope', () => 1)))

    expect(entry.status).toBe('failed')
    expect(entry.detail).toMatch(/activate: unknown hook 'nope' \(this SDK defines: t\.transform\)$/)
  })

  it('hooks.on registers, the returned function unregisters', async () => {
    const host = createFakeHost({ hooks: HOOKS })
    let off: () => void = () => {}

    await host.activate(
      ext(api => {
        off = api.hooks.on('t.transform', (p: { text: string }) => ({ text: p.text.toUpperCase() }))
      })
    )

    expect(await host.run('t.transform', { text: 'a' })).toEqual({ text: 'A' })
    off()
    expect(await host.run('t.transform', { text: 'a' })).toEqual({ text: 'a' })
  })

  it('a hook that throws three times disables its extension and stops being called', async () => {
    const host = createFakeHost({ hooks: HOOKS })
    let calls = 0

    await host.activate(
      ext(
        api =>
          void api.hooks.on('t.transform', () => {
            calls += 1
            throw new Error('always')
          })
      )
    )

    for (let i = 0; i < 5; i++) {
      expect(await host.run('t.transform', { text: 'a' })).toEqual({ text: 'a' })
    }

    expect(calls).toBe(3)
    expect(host.runtime.get('fake/ext')?.status).toBe('disabled')
    expect(host.notices.map(n => n.text)).toEqual([
      'extension fake/ext: hook t.transform failed: always (/extensions errors fake/ext)',
      'extension fake/ext disabled: 3 failures, last: hook t.transform: always (re-enable with /extensions enable fake/ext)'
    ])
  })

  it('commands: validated names, one owner per name, run with print', async () => {
    const host = createFakeHost()
    const hello = { help: 'say hi', run: (args: string, ctx: { print(t: string): void }) => ctx.print(`hi ${args}`) }

    await host.activate(
      ext(api => api.registerCommand('hello', hello)),
      { name: 'one' }
    )
    expect(await host.command('hello', 'there')).toEqual(['hi there'])

    const clash = await host.activate(
      ext(api => api.registerCommand('hello', hello)),
      { name: 'two' }
    )

    expect(clash.status).toBe('failed')
    expect(clash.detail).toMatch(/command 'hello' is already registered by fake\/one$/)

    const bad = await host.activate(
      ext(api => api.registerCommand('Bad Name', hello)),
      { name: 'three' }
    )

    expect(bad.detail).toMatch(/invalid command name 'Bad Name'/)
    expect(await host.command('missing')).toEqual([])
  })

  it('a command that throws is a failure of its extension, not of the host', async () => {
    const host = createFakeHost()

    await host.activate(
      ext(api =>
        api.registerCommand('boom', {
          help: 'x',
          run: () => {
            throw new Error('kaput')
          }
        })
      )
    )

    expect(await host.command('boom')).toEqual([])
    expect(host.runtime.get('fake/ext')?.errors[0]).toMatch(/command boom: kaput$/)
  })

  it('an activate that never settles times out and leaves the extension failed', async () => {
    const host = createFakeHost({ activateTimeoutMs: 30 })
    const entry = await host.activate(ext(() => new Promise(() => {})))

    expect(entry.status).toBe('failed')
  })
})
