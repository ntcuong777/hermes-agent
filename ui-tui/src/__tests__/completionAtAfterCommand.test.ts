import { describe, expect, it } from 'vitest'

import { completionRequestForInput } from '../hooks/useCompletion.js'

// B5. A prompt that starts with a command or a skill (`/caveman read @sr please`) is a slash command to the
// completer, so an `@` typed later in it was handed to the command-argument completer, which knows no files:
// the file picker never opened. The `@` reference is prose-level, so it opens wherever it is typed.
describe('@ references after a leading /command or /skill', () => {
  it('open the file picker at the caret, mid-prompt', () => {
    expect(completionRequestForInput('/caveman read @sr please', 17)).toMatchObject({
      method: 'complete.path',
      params: { word: '@sr' },
      replaceFrom: 14,
      replaceTo: 17
    })
  })

  it('open the @ menu for a bare @', () => {
    expect(completionRequestForInput('/caveman fix @ now', 14)).toMatchObject({
      method: 'complete.path',
      params: { word: '@' },
      replaceFrom: 13,
      replaceTo: 14
    })
  })

  it('open it at the end of the text too', () => {
    expect(completionRequestForInput('/caveman read @sr')).toMatchObject({
      method: 'complete.path',
      params: { word: '@sr' },
      replaceFrom: 14,
      replaceTo: 17
    })
  })

  it('work after a skill with an @file: prefix', () => {
    expect(completionRequestForInput('/writing-skills check @file:src/')).toMatchObject({
      method: 'complete.path',
      params: { word: '@file:src/' }
    })
  })
})

describe('command arguments keep going to the command completer', () => {
  it('a subcommand', () => {
    expect(completionRequestForInput('/cron ad')).toMatchObject({ method: 'complete.slash', params: { text: '/cron ad' } })
  })

  it('an argument that merely contains a slash is not a file path', () => {
    expect(completionRequestForInput('/skills install owner/na')).toMatchObject({ method: 'complete.slash' })
  })

  it('a path-like word after a command is still the command completer, as before', () => {
    expect(completionRequestForInput('/caveman read src/ma')).toMatchObject({ method: 'complete.slash' })
  })

  it('/model still opens the picker, not completion', () => {
    expect(completionRequestForInput('/model gpt @sr')).toBeNull()
  })

  it('an inline /skill at the caret is still a skill reference', () => {
    expect(completionRequestForInput('/caveman use /writing')).toMatchObject({
      method: 'complete.slash',
      skillsOnly: true
    })
  })
})
