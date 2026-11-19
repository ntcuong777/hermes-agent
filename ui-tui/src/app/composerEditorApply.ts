/** fork: application glue for the composer editor extension (tui-ports registers the editor).
 *  The extension answers `composerEditor` plans (text to edit, or an assign to write); this
 *  module writes an assign into the composer's state and runs its paste-collapse request, so
 *  upstream-owned useComposerState keeps only the seam calls. */
import { useEffect, type RefObject } from 'react'
import { useStore } from '@nanostores/react'

import type { ComposerToken, StateSetter } from './interfaces.js'
import type { GatewayClient } from '../gatewayClient.js'
import { pasteTokenLabel } from '../lib/text.js'
import { composerEditor, hasComposerEditor, type ComposerEditorAssign } from '../sdk/ext/composerHooks.js'
import { captureDestination, isCurrentDestination, type SubmissionDestination } from './submissionDestination.js'
import { getUiState, $uiSessionId } from './uiStore.js'

/** The composer state writers an assign needs. */
export interface ComposerEditorApplyDeps {
  gw: GatewayClient
  setComposerTokens: StateSetter<ComposerToken[]>
  setInput: StateSetter<string>
  setInputBuf: StateSetter<string[]>
  sys: (message: string) => void
}

/** Write an editor assign into the composer: sys line, tokens, input, buffer, then the
 *  assign's paste-collapse round-trip (the path lands on the token it named, only while the
 *  destination is still current). */
export function applyComposerEditorAssign(
  assign: ComposerEditorAssign,
  destination: SubmissionDestination,
  deps: ComposerEditorApplyDeps
): void {
  const { gw, setComposerTokens, setInput, setInputBuf, sys } = deps

  if (assign.sys) {
    sys(assign.sys)
  }

  if (assign.tokens) {
    setComposerTokens(assign.tokens as ComposerToken[])
  }

  if (assign.inputBuf) {
    setInputBuf(assign.inputBuf)
  }

  if (assign.input != null) {
    setInput(assign.input)
  }

  if (!assign.pasteCollapse) {
    return
  }

  const token = assign.pasteCollapse.token

  void gw
    .request<{ path?: string }>('paste.collapse', { text: assign.pasteCollapse.text })
    .then(r => {
      const path = r?.path

      if (!path || !isCurrentDestination(destination)) {
        return
      }

      setComposerTokens(prev => prev.map(tk => (tk === token ? { ...tk, path } : tk)))
    })
    .catch(() => {})
}

/** Seam: an editor plugin may return an assign when the composer is empty. Core writes it and
 *  does not keep the draft. Runs on session and composer changes; a no-op when no editor is
 *  registered. */
export function useComposerEditorIdle(opts: {
  deps: ComposerEditorApplyDeps
  input: string
  inputBuf: string[]
  tokensRef: RefObject<ComposerToken[]>
  trimTokens: (tokens: ComposerToken[]) => ComposerToken[]
}): void {
  const { deps, input, inputBuf, tokensRef, trimTokens } = opts
  const sid = useStore($uiSessionId)
  const hasEditor = hasComposerEditor()

  useEffect(() => {
    if (!hasEditor) {
      return
    }

    const plan = composerEditor({
      composerEmpty: input.length === 0 && inputBuf.length === 0,
      holds: destination => isCurrentDestination(destination as SubmissionDestination),
      phase: 'idle',
      tokens: tokensRef.current,
      trimTokens
    })

    if (plan && 'assign' in plan && plan.assign) {
      applyComposerEditorAssign(plan.assign, captureDestination(), deps)
    }
  }, [deps, hasEditor, input, inputBuf, sid, tokensRef, trimTokens])
}

/** The text the external editor starts from: the editor extension may transform the joined
 *  draft; without one the joined draft itself. */
export function composerEditorPreparedText(joined: string, tokens: ComposerToken[]): string {
  if (!hasComposerEditor()) {
    return joined
  }

  const plan = composerEditor({ phase: 'prepare', text: joined, tokens })

  return plan && 'text' in plan ? plan.text : joined
}

/** The saved-phase editor plan as an assign, or null when the extension declines (core's own
 *  save path runs). Editor-only: the payload is built here so the caller names no editor fields. */
export function composerEditorSavedAssign(input: {
  destination: unknown
  destinationOk: boolean
  joined: string
  saved: string
  tokens?: Readonly<ComposerToken[]>
  trimTokens: (tokens: ComposerToken[]) => ComposerToken[]
}): ComposerEditorAssign | null {
  if (!hasComposerEditor()) {
    return null
  }

  const ui = getUiState()
  const { destination, destinationOk, joined, saved, tokens, trimTokens } = input
  const plan = composerEditor({
    collapseChars: ui.pasteCollapseChars,
    collapseLines: ui.pasteCollapseLines,
    destination,
    destinationOk,
    joined,
    label: pasteTokenLabel,
    phase: 'saved',
    saved,
    tokens,
    trimTokens
  })

  return plan && 'assign' in plan && plan.assign ? plan.assign : null
}
