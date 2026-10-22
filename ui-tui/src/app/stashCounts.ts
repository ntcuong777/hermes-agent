import { atom } from 'nanostores'

/** How many drafts the current session's stash and the profile's stash hold, for the status bar. */
export const $stashCounts = atom<{ global: number; session: number }>({ global: 0, session: 0 })

type SlashRpc = (
  method: 'slash.exec',
  params: { command: string; session_id: string }
) => Promise<null | { output?: string } | undefined>

/** Ask the server for the counts of `sid`'s session stash and the profile stash. A failed call or an
 *  answer that is not the counts (plugin off, error text) leaves the last counts in place. */
export async function refreshStashCounts(rpc: SlashRpc, sid: null | string): Promise<void> {
  if (!sid) {
    return
  }

  try {
    const reply = await rpc('slash.exec', { command: 'stash counts', session_id: sid })
    const counts: unknown = JSON.parse(reply?.output ?? '')

    if (
      typeof counts === 'object' &&
      counts !== null &&
      'session' in counts &&
      'global' in counts &&
      typeof counts.session === 'number' &&
      typeof counts.global === 'number'
    ) {
      $stashCounts.set({ global: counts.global, session: counts.session })
    }
  } catch {
    // keep the last counts
  }
}
