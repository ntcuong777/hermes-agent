import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ModelPicker } from '../components/modelPicker.js'
import { DEFAULT_THEME } from '../theme.js'

// The key stage refuses model.save_key with the code-skew error whose only actionable
// text is the tail ("hermes gateway restart"). Truncating that row to one line hid the
// remediation and sent the user re-creating a perfectly valid key (#99859 report).

const SKEW =
  'This gateway is running code from e43f696935 but the checkout on disk is now 585b48e0e1. ' +
  'Switching models would risk a stale-module crash — restart the gateway to load the new code: hermes gateway restart'

const provider = {
  auth_type: 'api_key',
  authenticated: false,
  key_env: 'CURSOR_API_KEY',
  models: [],
  name: 'Cursor',
  slug: 'cursor'
}

describe('ModelPicker key-stage error rendering', () => {
  it('wraps a long save_key refusal so the restart command stays visible', async () => {
    const request = vi.fn(async (method: string) => {
      if (method === 'model.options') {
        return { providers: [provider] }
      }

      if (method === 'model.save_key') {
        throw Object.assign(new Error(SKEW), { code: 5098 })
      }

      throw Object.assign(new Error(`unknown method: ${method}`), { code: -32601 })
    })

    const stdin = Object.assign(new PassThrough(), { isTTY: true, setRawMode: vi.fn(), ref: vi.fn(), unref: vi.fn() })
    const stdout = Object.assign(new PassThrough(), { columns: 80, rows: 30, isTTY: false })
    const out = { text: '' }
    stdout.on('data', chunk => {
      out.text += chunk.toString()
    })

    const element = (
      <ModelPicker
        gw={{ isCanonical: false, request } as any}
        onCancel={vi.fn()}
        onSelect={vi.fn()}
        t={DEFAULT_THEME}
      />
    )

    const instance = renderSync(element, {
      stdin: stdin as any,
      stdout: stdout as any,
      stderr: new PassThrough() as any,
      patchConsole: false
    })

    try {
      // Provider list lands; Enter picks the unauthenticated Cursor row -> key stage.
      await vi.waitFor(() => {
        instance.rerender(React.cloneElement(element))
        expect(out.text).toContain('Cursor')
      })
      stdin.write('\r')
      // The key stage's own title — the provider list also prints the key env var.
      await vi.waitFor(() => {
        out.text = ''
        instance.rerender(React.cloneElement(element))
        expect(out.text).toContain('Configure Cursor')
      })

      // Type a key (let the state flush so Enter sees it), then submit.
      stdin.write('sk-test-key')
      await vi.waitFor(() => {
        out.text = ''
        instance.rerender(React.cloneElement(element))
        expect(out.text).toContain('•')
      })
      stdin.write('\r')
      await vi.waitFor(() => {
        out.text = ''
        instance.rerender(React.cloneElement(element))
        expect(request).toHaveBeenCalledWith('model.save_key', { api_key: 'sk-test-key', slug: 'cursor' })
        expect(out.text).toContain('error:')
      })

      // Wrapping, not truncation: the tail (the actionable command) must render.
      expect(out.text).toContain('hermes gateway restart')
      expect(out.text).not.toContain('…')
    } finally {
      instance.unmount()
    }
  })
})
