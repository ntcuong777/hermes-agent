import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import { stripAnsi } from '@hermes/shared/ansi'
import React from 'react'
import { expect, it, vi } from 'vitest'

import { type Choice, reduceChoice, showChoices } from '../app/choicePicker.js'
import { getOverlayState, resetOverlayState } from '../app/overlayStore.js'
import { ActiveWidgetSlot, dispatchWidgetInput } from '../sdk/host.js'
import type { WidgetInput } from '../sdk/types.js'

const key = (ch: string, value: Partial<WidgetInput['key']> = {}): WidgetInput => ({ ch, key: value }) as WidgetInput

const choices: Choice[] = [
  { label: 'first draft', select: '/pick 1', drop: '/drop 1' },
  { label: 'second draft', select: '/pick 2', drop: '/drop 2' }
]

const state = (rows: readonly Choice[] = choices) => ({
  acted: null,
  act: vi.fn(),
  choices: rows,
  selected: 1,
  title: 'T'
})

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

it('moves, picks by Enter or digit, drops with x or d, and cancels with Esc', () => {
  expect(reduceChoice(state(), key('', { upArrow: true }))?.selected).toBe(0)
  expect(reduceChoice(state(), key('', { downArrow: true }))?.selected).toBe(1)
  expect(reduceChoice(state(), key('', { return: true }))?.acted).toBe('/pick 2')
  expect(reduceChoice(state(), key('1'))?.acted).toBe('/pick 1')
  expect(reduceChoice(state(), key('x'))?.acted).toBe('/drop 2')
  expect(reduceChoice(state(), key('d'))?.acted).toBe('/drop 2')
  expect(reduceChoice(state(), key('', { escape: true }))).toBeNull()
})

it('ignores a digit past the list, an unknown key, and x on a row with no drop command', () => {
  const s = state()
  expect(reduceChoice(s, key('7'))).toBe(s)
  expect(reduceChoice(s, key('q'))).toBe(s)

  const bare = state([{ label: 'only', select: '/pick 1' }])
  expect(reduceChoice({ ...bare, selected: 0 }, key('x'))?.acted).toBeNull()
})

it('renders the title and numbered rows, and runs only the command of the row picked', async () => {
  resetOverlayState()
  const act = vi.fn()
  showChoices(choices, 'Stash · session (2/9)', 1, act)
  const { instance, text } = mountPicker()

  try {
    await vi.waitFor(() => expect(text()).toContain('Stash · session (2/9)'), { timeout: 3000 })
    expect(text()).toContain('1. first draft')
    expect(text()).toContain('2. second draft')
    expect(text()).toContain('x delete')
    expect(act).not.toHaveBeenCalled()
    dispatchWidgetInput(key('1'))
    await vi.waitFor(() => expect(act).toHaveBeenCalledExactlyOnceWith('/pick 1'), { timeout: 3000 })
    expect(getOverlayState().widget).toBeNull()
  } finally {
    instance.unmount()
    instance.cleanup()
    resetOverlayState()
  }
})

it('x runs the drop command of the highlighted row, not its select', async () => {
  resetOverlayState()
  const act = vi.fn()
  showChoices(choices, 'T', 1, act)
  const { instance, text } = mountPicker()

  try {
    await vi.waitFor(() => expect(text()).toContain('x delete'), { timeout: 3000 })
    dispatchWidgetInput(key('x'))
    await vi.waitFor(() => expect(act).toHaveBeenCalledExactlyOnceWith('/drop 2'), { timeout: 3000 })
  } finally {
    instance.unmount()
    instance.cleanup()
    resetOverlayState()
  }
})

it('opens nothing for an empty list', () => {
  resetOverlayState()
  showChoices([], 'T', 0, vi.fn())
  expect(getOverlayState().widget).toBeNull()
})

it('offers the delete key only when a row can be deleted', async () => {
  resetOverlayState()
  showChoices([{ label: 'only', select: '/pick 1' }], 'T', 0, vi.fn())
  const { instance, text } = mountPicker()

  try {
    await vi.waitFor(() => expect(text()).toContain('1. only'), { timeout: 3000 })
    expect(text()).not.toContain('x delete')
  } finally {
    instance.unmount()
    instance.cleanup()
    resetOverlayState()
  }
})

it('Esc closes without running anything', () => {
  resetOverlayState()
  const act = vi.fn()
  showChoices(choices, 'T', 0, act)
  dispatchWidgetInput(key('', { escape: true }))

  expect(getOverlayState().widget).toBeNull()
  expect(act).not.toHaveBeenCalled()
})
