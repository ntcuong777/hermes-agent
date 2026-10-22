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
  dropped: number | null
  apply: (index: number, text: string) => void
  drop: (index: number, text: string) => void
}

export function selectStash(state: StashPickerState, { ch, key }: WidgetInput): StashPickerState | null {
  if (key.escape) {
    return null
  }

  if (key.upArrow) {
    return { ...state, selected: Math.max(0, state.selected - 1) }
  }

  if (key.downArrow) {
    return { ...state, selected: Math.min(state.entries.length - 1, state.selected + 1) }
  }

  if (key.return) {
    return { ...state, chosen: state.selected }
  }
  if (ch === 'x' || ch === 'd') {
    return { ...state, dropped: state.selected }
  }
  const number = /^[1-9]$/.test(ch) ? Number(ch) - 1 : -1

  return number >= 0 && number < state.entries.length ? { ...state, chosen: number } : state
}

function StashPicker({ state, rows }: { state: StashPickerState; rows: number }) {
  const applied = useRef(false)
  useEffect(() => {
    const index = state.chosen ?? state.dropped

    if (index === null || applied.current) {
      return
    }

    if ($overlayState.get().widget?.state !== state) {
      return
    }
    applied.current = true
    closeWidget()
    ;(state.chosen === null ? state.drop : state.apply)(index, state.entries[index]!)
  }, [state])
  const visible = Math.max(1, rows - 6)
  const start = Math.max(0, state.selected - visible + 1)

  return (
    <Box borderStyle="round" flexDirection="column">
      <Text bold>
        Stash ({state.entries.length}/{state.maximum}) — ↑↓ Enter · 1–9 · x delete · Esc cancel
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

function parseSnapshot(output: string): { entries: readonly string[]; max: number } {
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

  return { entries: snapshot.entries, max: snapshot.max }
}

export function showStashPicker(
  output: string,
  apply: StashPickerState['apply'],
  drop: StashPickerState['drop'] = () => {}
): void {
  const { entries, max } = parseSnapshot(output)

  if (entries.length === 0) {
    return
  }
  openWidget(stashPicker, {
    entries,
    maximum: max,
    selected: entries.length - 1,
    chosen: null,
    dropped: null,
    apply,
    drop
  })
}

/** Alt+S on an empty composer, like Pi's stash extension: a lone entry is restored at once
 *  (no list to pick from), several open the picker, none reports empty. */
export function openStashes(
  output: string,
  apply: StashPickerState['apply'],
  drop: StashPickerState['drop']
): 'applied' | 'empty' | 'picker' {
  const { entries } = parseSnapshot(output)

  if (entries.length === 0) {
    return 'empty'
  }

  if (entries.length === 1) {
    apply(0, entries[0]!)

    return 'applied'
  }
  showStashPicker(output, apply, drop)

  return 'picker'
}

export async function applyStashSelection(selection: {
  index: number
  text: string
  accept: (text: string) => boolean
  acknowledge: (argument: string) => Promise<void>
}): Promise<void> {
  if (!selection.accept(selection.text)) {
    return
  }
  await selection.acknowledge(JSON.stringify({ index: selection.index, text: selection.text }))
}
