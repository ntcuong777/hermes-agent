import { describe, expect, it } from 'vitest'

import { InputEvent } from '../../packages/hermes-ink/src/ink/events/input-event.js'
import { INITIAL_STATE, parseMultipleKeypresses } from '../../packages/hermes-ink/src/ink/parse-keypress.js'
import { isTypedTextEvent } from '../components/textInput.js'

function eventFor(sequence: string) {
  const [keys] = parseMultipleKeypresses(INITIAL_STATE, sequence)

  return new InputEvent(keys[0]!)
}

// Alt+S (ESC s) arrives as input "s" with meta set. The composer must not type
// it: meta letters are shortcuts (Alt+S stash, Alt+G ...), not text.
describe('Alt-letter is a shortcut, not typed text', () => {
  it('Alt+S is not typed text', () => {
    expect(isTypedTextEvent(eventFor('\x1bs'))).toBe(false)
  })

  it('plain s is typed text', () => {
    expect(isTypedTextEvent(eventFor('s'))).toBe(true)
  })

  it('pasted text is typed even with a control byte', () => {
    expect(isTypedTextEvent(eventFor('\x1b[200~hi\x1b[201~'))).toBe(true)
  })
})
