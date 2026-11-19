import { Box, Text } from '@hermes/ink'
import { useEffect, useRef } from 'react'

import { closeWidget, openWidget } from '../sdk/host.js'
import { defineWidgetApp } from '../sdk/registry.js'
import type { WidgetInput } from '../sdk/types.js'

import { $overlayState } from './overlayStore.js'

export interface Choice {
  /** Slash command run on `x`; a row without one ignores the key. */
  drop?: string
  label: string
  /** Slash command run on Enter or the row's digit. */
  select: string
}

interface ChoicePickerState {
  choices: readonly Choice[]
  /** The command Enter or `x` chose, handed to `act` once; null while still browsing. */
  acted: null | string
  act: (command: string) => void
  selected: number
  title: string
}

export const reduceChoice = (state: ChoicePickerState, { ch, key }: WidgetInput): ChoicePickerState | null => {
  const last = state.choices.length - 1
  const row = state.choices[state.selected]!
  const digit = /^[1-9]$/.test(ch) ? Number(ch) - 1 : -1
  const pick = (index: number) => ({ ...state, acted: state.choices[index]!.select })

  return key.escape
    ? null
    : key.upArrow
      ? { ...state, selected: Math.max(0, state.selected - 1) }
      : key.downArrow
        ? { ...state, selected: Math.min(last, state.selected + 1) }
        : key.return
          ? pick(state.selected)
          : (ch === 'x' || ch === 'd') && row.drop
            ? { ...state, acted: row.drop }
            : digit >= 0 && digit <= last
              ? pick(digit)
              : state
}

function ChoicePicker({ state, rows }: { state: ChoicePickerState; rows: number }) {
  const done = useRef(false)

  useEffect(() => {
    if (!state.acted || done.current || $overlayState.get().widget?.state !== state) {
      return
    }

    done.current = true
    closeWidget()
    state.act(state.acted)
  }, [state])

  const visible = Math.max(1, rows - 6)
  const start = Math.max(0, state.selected - visible + 1)
  const dropKey = state.choices.some(c => c.drop) ? ' · x delete' : ''

  return (
    <Box borderStyle="round" flexDirection="column">
      <Text bold>
        {state.title}: ↑↓ Enter · 1–9{dropKey} · Esc cancel
      </Text>
      {state.choices.slice(start, start + visible).map((choice, offset) => (
        <Text inverse={start + offset === state.selected} key={start + offset} wrap="truncate-end">
          {start + offset + 1}. {choice.label.replace(/\s+/g, ' ')}
        </Text>
      ))}
    </Box>
  )
}

const choicePicker = defineWidgetApp<ChoicePickerState>({
  help: 'A list a command asked you to pick from',
  id: 'choice-picker',
  init: () => null,
  reduce: reduceChoice,
  render: ({ state, rows }) => <ChoicePicker rows={rows} state={state} />,
  usage: 'Opened by a command that offers a list (see display.tui_keybinds).'
})

/** Open the picker a `choices` directive describes; `act` runs the command of the row Enter (or `x`) hit.
 *  Nothing to pick from opens nothing. */
export const showChoices = (
  choices: readonly Choice[],
  title: string,
  selected: number | undefined,
  act: (command: string) => void
): void =>
  choices.length
    ? openWidget(choicePicker, {
        acted: null,
        act,
        choices,
        selected: Math.min(Math.max(0, selected ?? 0), choices.length - 1),
        title
      })
    : undefined
