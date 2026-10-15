import { describe, expect, it } from 'vitest'

import { applyCompletion, completionCursor, completionToApplyOnSubmit } from '../domain/slash.js'
import { completionRequestForInput } from '../hooks/useCompletion.js'

// The caret is not always at the end: the person types `@` in the middle of a prompt they already wrote.
describe('completion at the caret, not only at the end of the text', () => {
  it('offers paths for an @ word in the middle of the text', () => {
    expect(completionRequestForInput('read @sr please', 8)).toMatchObject({
      method: 'complete.path',
      params: { word: '@sr' },
      replaceFrom: 5,
      replaceTo: 8
    })
  })

  it('a bare @ in the middle opens the @ menu', () => {
    expect(completionRequestForInput('fix @ now', 5)).toMatchObject({
      method: 'complete.path',
      params: { word: '@' },
      replaceFrom: 4,
      replaceTo: 5
    })
  })

  it('a path word in the middle completes too', () => {
    expect(completionRequestForInput('open src/ma and tell me', 11)).toMatchObject({
      method: 'complete.path',
      params: { word: 'src/ma' },
      replaceFrom: 5
    })
  })

  it('an inline /skill in the middle completes as a skill', () => {
    expect(completionRequestForInput('run /cle then stop', 8)).toMatchObject({
      method: 'complete.slash',
      params: { text: '/cle' },
      replaceFrom: 5,
      skillsOnly: true
    })
  })

  it('plain words before the caret still offer nothing', () => {
    expect(completionRequestForInput('hello @there', 5)).toBeNull()
  })

  it('with the caret at the end nothing changes', () => {
    expect(completionRequestForInput('read @sr')).toMatchObject({ replaceFrom: 5, replaceTo: 8 })
    expect(completionRequestForInput('read @sr', 8)).toEqual(completionRequestForInput('read @sr'))
  })

  it('a caret past the end is treated as the end', () => {
    expect(completionRequestForInput('read @sr', 99)).toEqual(completionRequestForInput('read @sr'))
  })
})

describe('applying a completion keeps the text after the caret', () => {
  it('splices the row in and keeps the rest of the prompt', () => {
    expect(applyCompletion('read @sr please', '@file:src/', 5, 8)).toBe('read @file:src/ please')
  })

  it('the caret lands right after the inserted text', () => {
    expect(completionCursor('read @sr please', '@file:src/', 5)).toBe(15)
  })

  it('without an end it replaces to the end, as before', () => {
    expect(applyCompletion('read @sr', '@file:src/', 5)).toBe('read @file:src/')
  })

  it('Enter accepts a mid-text completion without eating the tail', () => {
    expect(completionToApplyOnSubmit('read @sr please', '@file:src/', 5, () => false, 8)).toBe('read @file:src/ please')
  })

  it('an inline skill keeps its own slash and the tail', () => {
    expect(applyCompletion('run /cle then stop', '/clean', 5, 8)).toBe('run /clean then stop')
    expect(completionCursor('run /cle then stop', '/clean', 5)).toBe(10)
  })
})
