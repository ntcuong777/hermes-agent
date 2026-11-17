// Status-bar text the TUI itself decides. Cost and quota are a plugin's (usage.status_fields,
// hook transform_status_bar_fields): the StatusRule only places what the server sends.

import type { Usage } from '@hermes/shared/gateway-events'

/** One plugin-supplied segment: `{id, text, tone?, min_cols?}`, shown when `id` is listed in display.status_bar.fields. */
export type StatusField = NonNullable<Usage['status_fields']>[number]

/** `📋N` for the session stash, `📋N+M` once the profile stash holds something too; '' when both are empty. */
export const stashLabel = (counts: { global: number; session: number }): string =>
  counts.session + counts.global === 0
    ? ''
    : counts.global > 0
      ? `📋${counts.session}+${counts.global}`
      : `📋${counts.session}`
