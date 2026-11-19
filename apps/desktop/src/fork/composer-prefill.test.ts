/** fork: composer prefill routing (fork/composer-prefill.ts) at the draft-bus level. Moved out
 *  of upstream's focus.test.ts, which stays upstream's own. The legacy store write is pinned by
 *  upstream's own use-prompt-actions tests; this file pins the bus semantics. */
import { afterEach, describe, expect, it } from 'vitest'

import { markActiveComposer, onComposerDraftRequests } from '@/app/chat/composer/focus'
import { requestComposerPrefill } from '@/fork/composer-prefill'

const disposers: (() => void)[] = []

function mountDraft(id: string, active = false) {
  const state = { wrote: null as null | string }
  disposers.push(
    onComposerDraftRequests(
      { getIds: () => [id], isActive: () => active },
      {
        read: () => '',
        write: text => {
          state.wrote = text

          return true
        }
      }
    )
  )

  return state
}

function mountSurface(target: string, hidden = false) {
  const layer = document.createElement('div')
  layer.toggleAttribute('data-pane-hidden', hidden)
  const surface = document.createElement('div')
  surface.dataset.composerTarget = target
  layer.append(surface)
  document.body.append(layer)
}

afterEach(() => {
  for (const off of disposers.splice(0)) {
    off()
  }
  document.body.innerHTML = ''
  markActiveComposer('main')
})

const settle = () => new Promise(resolve => window.setTimeout(resolve, 120))

describe('requestComposerPrefill', () => {
  it('prefills the addressed session; only a payload with no session identity may use the active composer', async () => {
    const addressed = mountDraft('sess-a')
    requestComposerPrefill(['sess-a'], 'stashed draft')
    await settle()
    expect(addressed.wrote).toBe('stashed draft')

    // No id-addressed surface answers (an unmounted target session): the text
    // is DROPPED on the bus, never painted into whatever session is on screen —
    // 'active' is whatever session the bus routes to, and an Enter there would send
    // another session's restored message.
    mountSurface('main')
    const active = mountDraft('sess-live', true)
    requestComposerPrefill(['nobody'], 'undo text')
    await settle()
    expect(active.wrote).toBeNull()

    // A payload with no session identity at all (the degenerate-slash
    // restore, captured from the submitting composer) IS the active composer.
    requestComposerPrefill([], 'degenerate payload')
    await settle()
    expect(active.wrote).toBe('degenerate payload')
  })
})
