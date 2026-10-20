import { Box, Text } from '@hermes/ink'
import { useEffect, useRef } from 'react'

import { closeWidget, openWidget } from '../sdk/host.js'
import { defineWidgetApp } from '../sdk/registry.js'
import type { WidgetInput } from '../sdk/types.js'

import { $overlayState } from './overlayStore.js'

interface StashPickerState {
  entries: readonly string[]
  maximum: number
  selected: number
  chosen: number | null
  apply: (index: number, text: string) => void
}

export function selectStash(state: StashPickerState, { ch, key }: WidgetInput): StashPickerState | null {
  if (key.escape) {return null}

  if (key.upArrow) {return { ...state, selected: Math.max(0, state.selected - 1) }}

  if (key.downArrow) {return { ...state, selected: Math.min(state.entries.length - 1, state.selected + 1) }}

  if (key.return) {return { ...state, chosen: state.selected }}
  const number = /^[1-9]$/.test(ch) ? Number(ch) - 1 : -1

  return number >= 0 && number < state.entries.length ? { ...state, chosen: number } : state
}

function StashPicker({ state, rows }: { state: StashPickerState; rows: number }) {
  const applied = useRef(false)
  useEffect(() => {
    if (state.chosen === null || applied.current) {return}

    if ($overlayState.get().widget?.state !== state) {return}
    applied.current = true
    closeWidget()
    state.apply(state.chosen, state.entries[state.chosen]!)
  }, [state])
  const visible = Math.max(1, rows - 6)
  const start = Math.max(0, state.selected - visible + 1)

  return (
    <Box borderStyle="round" flexDirection="column">
      <Text bold>
        Stash ({state.entries.length}/{state.maximum}) — ↑↓ Enter · 1–9 · Esc cancel
      </Text>
      {state.entries.slice(start, start + visible).map((text, offset) => (
        <Text inverse={start + offset === state.selected} key={start + offset} wrap="truncate-end">
          {start + offset + 1}. {text.replace(/\s+/g, ' ')}
        </Text>
      ))}
    </Box>
  )
}

const stashPicker = defineWidgetApp<StashPickerState>({
  id: 'stash-picker',
  help: 'Select a stashed draft (Alt+S on an empty composer)',
  usage: 'Press Alt+S on an empty composer to select a stash.',
  init: () => null,
  reduce: selectStash,
  render: ({ state, rows }) => <StashPicker rows={rows} state={state} />
})

export function showStashPicker(output: string, apply: StashPickerState['apply']): void {
  const snapshot: unknown = JSON.parse(output)

  if (
    typeof snapshot !== 'object' ||
    snapshot === null ||
    !('entries' in snapshot) ||
    !Array.isArray(snapshot.entries) ||
    !snapshot.entries.every((entry: unknown) => typeof entry === 'string') ||
    !('max' in snapshot) ||
    typeof snapshot.max !== 'number'
  ) {
    throw new Error('Invalid stash snapshot')
  }

  if (snapshot.entries.length === 0) {return}
  openWidget(stashPicker, {
    entries: snapshot.entries,
    maximum: snapshot.max,
    selected: snapshot.entries.length - 1,
    chosen: null,
    apply
  })
}

export async function applyStashSelection(selection: {
  index: number
  text: string
  accept: (text: string) => boolean
  acknowledge: (argument: string) => Promise<void>
}): Promise<void> {
  if (!selection.accept(selection.text)) {return}
  await selection.acknowledge(JSON.stringify({ index: selection.index, text: selection.text }))
}
