import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApprovalPrompt } from '../../components/prompts.js'
import { renderOutput, sameRender, stripAnsi } from '../../sdk/ext/frames.js'
import { DEFAULT_THEME } from '../../theme.js'

const req = { command: 'rm -rf build\nls', description: 'delete the build dir', pattern_key: 'rm' } as never
const props = { cols: 70, onChoice: () => {}, onTell: () => {}, req, t: DEFAULT_THEME }

beforeEach(() => void vi.spyOn(process.stdout, 'write').mockImplementation(() => true))

describe('frames: same props write the same bytes', () => {
  it('two renders of one component are byte-identical', async () => {
    const result = await sameRender(<ApprovalPrompt {...props} />, <ApprovalPrompt {...props} />)

    expect(result.textA).toContain('delete the build dir')
    expect(result.same).toBe(true)
  })

  it('a keystroke or another prop changes the bytes', async () => {
    const plain = await renderOutput(<ApprovalPrompt {...props} />)
    const keyed = await renderOutput(<ApprovalPrompt {...props} />, { keys: ['\u001b[B'] })

    const other = await renderOutput(
      <ApprovalPrompt {...props} req={{ ...(req as object), description: 'other' } as never} />
    )

    expect(keyed).not.toBe(plain)
    expect(other).not.toBe(plain)
    expect(stripAnsi(other)).toContain('other')
  })
})
