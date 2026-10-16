import { describe, expect, it } from 'vitest'

import { mutateCanonicalSession } from '../app/slash/canonicalSessionControls.js'
import { modelPickerCommand } from '../components/modelPicker.js'

// The model picker's third step emits `<model> --provider <slug> --reasoning <level> --session`.
// On a shared-gateway session `/model` is a `model` mutation that carries only model + provider, and
// the level is a separate `reasoning` mutation. The command must apply both, never refuse the pair.
function gateway(refuse = false) {
  const mutations: Array<{ operation: string; payload: Record<string, unknown> }> = []

  return {
    mutations,
    request: async (method: string, params?: Record<string, unknown>) => {
      if (method === 'session.resume') {
        return { revision: 7 + mutations.length, execution_generation: 3 }
      }

      mutations.push({ operation: params!.operation as string, payload: params!.payload as Record<string, unknown> })

      if (refuse && params!.operation === 'model') {
        throw Object.assign(new Error('refused'), { data: { reason: 'model_resolution_failed' } })
      }

      return { model: 'gpt-6.1-sol', revision: 8 + mutations.length, execution_generation: 3 }
    }
  }
}

describe('/model with --reasoning on a shared-gateway session', () => {
  it('sends the model and provider, then the level as its own mutation', async () => {
    const gw = gateway()
    const arg = modelPickerCommand('gpt-6.1-sol', 'openai-codex', false, 'high')

    await mutateCanonicalSession(gw, 's', 'model', arg)

    expect(gw.mutations).toEqual([
      { operation: 'model', payload: { model: 'gpt-6.1-sol', provider: 'openai-codex' } },
      { operation: 'reasoning', payload: { effort: 'high' } }
    ])
  })

  it('sends no reasoning mutation when the picker kept the current level', async () => {
    const gw = gateway()

    await mutateCanonicalSession(gw, 's', 'model', modelPickerCommand('gpt-6.1-sol', 'openai-codex', false, ''))

    expect(gw.mutations.map(m => m.operation)).toEqual(['model'])
  })

  it('leaves the level alone when the model switch is refused', async () => {
    const gw = gateway(true)

    await expect(
      mutateCanonicalSession(gw, 's', 'model', modelPickerCommand('gpt-6.1-sol', 'openai-codex', false, 'high'))
    ).rejects.toThrow('refused')
    expect(gw.mutations.map(m => m.operation)).toEqual(['model'])
  })

  it('still refuses a level the owner does not know, before any write', async () => {
    const gw = gateway()

    await expect(
      mutateCanonicalSession(gw, 's', 'model', 'gpt-6.1-sol --provider openai-codex --reasoning loud --session')
    ).rejects.toThrow(/reasoning/)
    expect(gw.mutations).toEqual([])
  })

  it('still refuses an option it has no verb for', async () => {
    const gw = gateway()

    await expect(mutateCanonicalSession(gw, 's', 'model', 'gpt-6.1-sol --bogus')).rejects.toThrow(
      'unsupported canonical model option: --bogus'
    )
  })
})
