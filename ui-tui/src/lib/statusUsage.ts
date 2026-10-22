// Status-bar text for session cost and the active account's subscription quota.
// Pure on purpose: the StatusRule only places the strings; what they say is decided (and tested) here.

import type { Usage } from '@hermes/shared/gateway-events'

export type QuotaWindow = NonNullable<Usage['quota']>[number]

/** `$0.42`, `~$0.42` for an estimate (a lower bound), `incl.` for a subscription that bills nothing, '' for no data. */
export const costLabel = (usage: Pick<Usage, 'cost_status' | 'cost_usd'>): string => {
  const usd = usage.cost_usd

  if (typeof usd === 'number' && Number.isFinite(usd) && usd > 0) {
    const mark = usage.cost_status === 'estimated' ? '~' : ''

    return usd < 0.01 ? `${mark}<$0.01` : `${mark}$${usd.toFixed(2)}`
  }

  if (usage.cost_status === 'included') {
    return 'incl.'
  }

  // Calls were made but none had a price (B17): say so instead of a blank bar.
  return usage.cost_status === 'unpriced' ? 'n/a' : ''
}

/** Short window name: the 5-hour session reads `5h`, the weekly cap `wk`, anything else its first word. */
export const quotaWindowName = (label: string): string => {
  const text = label.trim().toLowerCase()

  if (/session|5\s*-?\s*h(our)?/.test(text)) {
    return '5h'
  }

  if (/week/.test(text)) {
    return 'wk'
  }

  return text.split(/\s+/)[0]?.slice(0, 6) || '?'
}

const usedPercent = (w: QuotaWindow): number | null =>
  typeof w.used_percent === 'number' && Number.isFinite(w.used_percent)
    ? Math.max(0, Math.min(100, w.used_percent))
    : null

/** `5h 12% wk 40%` (percent used), '' when there is nothing to show. */
export const quotaLabel = (quota: Usage['quota']): string =>
  (quota ?? [])
    .flatMap(w => {
      const used = usedPercent(w)

      return used === null ? [] : [`${quotaWindowName(w.label ?? '')} ${Math.round(used)}%`]
    })
    .join(' ')

/** How close the fullest window is to its cap: drives the colour. */
export const quotaSeverity = (quota: Usage['quota']): 'bad' | 'ok' | 'warn' => {
  const worst = Math.max(0, ...(quota ?? []).map(w => usedPercent(w) ?? 0))

  return worst >= 90 ? 'bad' : worst >= 70 ? 'warn' : 'ok'
}

/** `📋N` for the session stash, `📋N+M` once the profile stash holds something too; '' when both are empty. */
export const stashLabel = (counts: { global: number; session: number }): string =>
  counts.session + counts.global === 0
    ? ''
    : counts.global > 0
      ? `📋${counts.session}+${counts.global}`
      : `📋${counts.session}`
