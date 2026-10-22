import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import { stripAnsi } from '@hermes/shared/ansi'
import React from 'react'
import { expect, it, vi } from 'vitest'

import { getOverlayState, resetOverlayState } from '../app/overlayStore.js'
import { showStashPicker } from '../app/stashPicker.js'
import { ActiveWidgetSlot, dispatchWidgetInput } from '../sdk/host.js'
import type { WidgetInput } from '../sdk/types.js'

const key = (ch: string, value: Partial<WidgetInput['key']> = {}): WidgetInput => ({ ch, key: value }) as WidgetInput

function mountPicker() {
  const stdout = Object.assign(new PassThrough(), { columns: 90, rows: 15, isTTY: false })
  let output = ''
  stdout.on('data', chunk => {
    output += chunk.toString()
  })

  const instance = renderSync(<ActiveWidgetSlot />, {
    stdin: new PassThrough() as NodeJS.ReadStream,
    stdout: stdout as NodeJS.WriteStream,
    stderr: new PassThrough() as NodeJS.WriteStream,
    patchConsole: false
  })

  return { instance, text: () => stripAnsi(output) }
}

it('renders count and numbered drafts, then applies only selected draft', async () => {
  resetOverlayState()
  const apply = vi.fn()
  showStashPicker('{"entries":["first draft","second draft"],"count":2,"max":9}', apply)
  const { instance, text } = mountPicker()

  try {
    await vi.waitFor(() => expect(text()).toContain('Stash · session (2/9)'), { timeout: 3000 })
    expect(text()).toContain('1. first draft')
    expect(text()).toContain('2. second draft')
    expect(apply).not.toHaveBeenCalled()
    dispatchWidgetInput(key('1'))
    await vi.waitFor(() => expect(apply).toHaveBeenCalledExactlyOnceWith(0, 'first draft'), { timeout: 3000 })
    expect(getOverlayState().widget).toBeNull()
  } finally {
    instance.unmount()
    instance.cleanup()
    resetOverlayState()
  }
})

it('cancels selection without applying or consuming a draft', () => {
  resetOverlayState()
  const apply = vi.fn()
  showStashPicker('{"entries":["kept draft"],"count":1,"max":9}', apply)
  dispatchWidgetInput(key('', { escape: true }))
  expect(getOverlayState().widget).toBeNull()
  expect(apply).not.toHaveBeenCalled()
})

it('x deletes the selected draft through drop, not apply', async () => {
  resetOverlayState()
  const apply = vi.fn()
  const drop = vi.fn()
  showStashPicker('{"entries":["first draft","second draft"],"count":2,"max":9}', apply, drop)
  const { instance, text } = mountPicker()

  try {
    await vi.waitFor(() => expect(text()).toContain('x delete'), { timeout: 3000 })
    dispatchWidgetInput(key('x'))
    await vi.waitFor(() => expect(drop).toHaveBeenCalledExactlyOnceWith(1, 'second draft'), { timeout: 3000 })
    expect(apply).not.toHaveBeenCalled()
    expect(getOverlayState().widget).toBeNull()
  } finally {
    instance.unmount()
    instance.cleanup()
    resetOverlayState()
  }
})

it('names the scope in the picker title', async () => {
  resetOverlayState()
  showStashPicker('{"entries":["a","b"],"count":2,"max":9}', vi.fn(), vi.fn(), 'global')
  const { instance, text } = mountPicker()

  try {
    await vi.waitFor(() => expect(text()).toContain('Stash · profile (2/9)'), { timeout: 3000 })
  } finally {
    instance.unmount()
    instance.cleanup()
    resetOverlayState()
  }
})
