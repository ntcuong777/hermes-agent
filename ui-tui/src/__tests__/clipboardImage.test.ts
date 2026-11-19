/** Clipboard image read lives in the tui-ports extension (`composer.clipboardImage`), not core. */
import { describe, expect, it } from 'vitest'

import * as clipboard from '../lib/clipboard.js'

describe('core clipboard', () => {
  it('does not read images itself', () => {
    expect('readClipboardImagePng' in clipboard).toBe(false)
  })
})
