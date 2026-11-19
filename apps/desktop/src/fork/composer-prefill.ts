/** fork: composer prefill routing (slash `prefill` directives — /undo's backed-up message,
 *  /stash pop's stored draft, the degenerate-slash restore).
 *
 *  Two writes, on purpose:
 *  - The legacy draft store write is upstream's own `setComposerDraft` semantics, kept so
 *    store-based consumers (and upstream's tests) keep working. No mounted composer reads
 *    that atom any more (the draft bus below owns live drafts; verified against the fork
 *    tree), so the write cannot paint a visible surface. Drift note: if upstream ever makes
 *    mounted composers re-read the atom live, this write becomes visible again and the
 *    session-addressed safety rule below would need the write dropped.
 *  - The draft-bus write routes the text to the addressed session's own mounted surface.
 *    A session-addressed prefill that no mounted surface accepted falls back to NOTHING on
 *    the bus: `active` is whatever session the focus bus routes to, and painting that
 *    composer with this session's text would put foreign words one Enter away from being
 *    sent — the visible notice already says what was restored. Only a payload with no
 *    session identity at all (the degenerate-slash restore, captured from the submitting
 *    composer before any session id exists) may use the active composer — there the
 *    submitting surface IS the active one. Fire-and-forget. */
import { requestComposerSetDraft } from '@/app/chat/composer/focus'
import { setComposerDraft } from '@/store/composer'

export const requestComposerPrefill = (ids: string[], text: string): void => {
  if (!text.trim()) {
    return
  }

  const hasIds = ids.some(id => typeof id === 'string' && id.trim())
  setComposerDraft(text)
  void requestComposerSetDraft(ids, text).then(ok => {
    if (!ok && !hasIds) {
      void requestComposerSetDraft([], text, { active: true })
    }
  })
}
