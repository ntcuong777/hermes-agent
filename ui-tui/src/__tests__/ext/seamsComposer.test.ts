/** Stage 1 seams: the paste and clipboard-image hook calls in app/useComposerState.ts. */
import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

describe('stage 1 seams of the extension host', () => {
  it('app/useComposerState.ts calls the paste and clipboard-image hooks', () => {
    const text = readFileSync(new URL('../../app/useComposerState.ts', import.meta.url), 'utf8')

    expect(text).toContain(
      "extensionHost.has('composer.paste') ? await transformPasteText(text, Boolean(bracketed)) : text"
    )
    expect(text).toContain('await clipboardImagePathFromExtensions()')
  })
})
