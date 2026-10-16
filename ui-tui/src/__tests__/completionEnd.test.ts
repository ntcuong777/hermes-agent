import { describe, expect, it } from 'vitest'

import { applyCompletion, completionCursor, completionEnd, completionToApplyOnSubmit } from '../domain/slash.js'
import { completionRequestForInput } from '../hooks/useCompletion.js'

// B4. A completion is asked for the text as it was at the caret, and its rows arrive a moment later. Characters
// typed in between belong to the word being completed. Tab and Enter used to splice up to the span recorded when
// the request was sent, so a character typed since stayed behind: `/writing-skillst` after picking `/writing-skills`.
const REQUESTED = '/caveman /writing-skill'
const ROW = '/writing-skills'
const caretAt = (value: string, cursor = value.length) => ({ cursor, value })

describe('where the completed word ends', () => {
  it('is the caret recorded for this very text', () => {
    const value = '/caveman /writing-skillt'

    expect(completionEnd(value, caretAt(value))).toBe(value.length)
    expect(completionEnd('read @sr please', caretAt('read @sr please', 8))).toBe(8)
  })

  it('is the end of the text when no caret was recorded for it', () => {
    expect(completionEnd('read @sr please', null)).toBe(15)
    expect(completionEnd('read @sr please', undefined)).toBe(15)
    expect(completionEnd('read @sr please', caretAt('some older text', 3))).toBe(15)
  })

  it('never lies past the end of the text', () => {
    expect(completionEnd('abc', caretAt('abc', 99))).toBe(3)
  })
})

describe('a character typed after the completion was requested (the screenshot)', () => {
  const request = completionRequestForInput(REQUESTED)
  const from = request!.replaceFrom
  const typedAhead = `${REQUESTED}t`

  it('is replaced with the rest of the word, not left behind', () => {
    const end = completionEnd(typedAhead, caretAt(typedAhead))

    expect(applyCompletion(typedAhead, ROW, from, end)).toBe('/caveman /writing-skills')
  })

  it('Enter accepts the same way', () => {
    const end = completionEnd(typedAhead, caretAt(typedAhead))

    expect(completionToApplyOnSubmit(typedAhead, ROW, from, () => false, end)).toBe('/caveman /writing-skills')
  })

  it('the caret lands right after the inserted word', () => {
    expect(completionCursor(typedAhead, ROW, from)).toBe('/caveman /writing-skills'.length)
  })
})

describe('text after the caret still stays (a completion in the middle of a prompt)', () => {
  it('keeps the tail the person wrote after the caret', () => {
    const value = 'run /cle then stop'

    expect(applyCompletion(value, '/clean', 5, completionEnd(value, caretAt(value, 8)))).toBe('run /clean then stop')
  })

  it('also when more was typed before the rows arrived', () => {
    const value = 'run /clea then stop'

    expect(applyCompletion(value, '/clean', 5, completionEnd(value, caretAt(value, 9)))).toBe('run /clean then stop')
  })
})
