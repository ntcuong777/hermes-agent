/** The slot table and the decision "may this extension's copy be mounted" (pure: no React, no I/O). */
import type { SlotDeclaration, SlotDef, SlotStatus } from './contract.js'
import slotsJson from './slots.json' with { type: 'json' }
import { UPSTREAM_HASHES } from './upstreamHashes.js'

export const SLOT_DEFS: Readonly<Record<string, SlotDef>> = Object.freeze(slotsJson.slots as Record<string, SlotDef>)

export { UPSTREAM_HASHES }

export interface SlotVerdict {
  detail: string
  /** `active` although the upstream region changed (on_drift apply): `detail` is the drift text. Absent when clean. */
  drifted?: true
  status: SlotStatus
}

/**
 * Compare what an extension recorded with what this TUI is. Order: unknown id, props contract, provenance, drift.
 * `upstream` is the table of region hashes embedded in this build (or a test's own table).
 */
export const checkSlot = (
  decl: SlotDeclaration,
  defs: Readonly<Record<string, SlotDef>> = SLOT_DEFS,
  upstream: Readonly<Record<string, string>> = UPSTREAM_HASHES
): SlotVerdict => {
  const def = defs[decl.id]

  if (!def) {
    return {
      detail: `unknown slot '${decl.id}' (this TUI defines: ${Object.keys(defs).join(', ') || 'none'})`,
      status: 'skipped-error'
    }
  }

  if (decl.props !== def.props) {
    return {
      detail: `slot '${decl.id}' props contract is v${def.props} in this TUI, the extension has v${decl.props}: update the extension`,
      status: 'skipped-incompatible'
    }
  }

  if (!decl.expectUpstream || !decl.upstreamCommit || !decl.reason) {
    return {
      detail: `slot '${decl.id}' has no provenance (reason, upstream commit, upstream hash): run tui-slot-check --record ${decl.id}`,
      status: 'skipped-error'
    }
  }

  const have = upstream[decl.id]

  if (!have) {
    return {
      detail: `this TUI build carries no upstream hash for '${decl.id}' (file ${def.file} unreadable at build time)`,
      status: 'skipped-error'
    }
  }

  if (have === decl.expectUpstream) {
    return { detail: '', status: 'active' }
  }

  const detail = `upstream ${def.file} changed since ${decl.upstreamCommit.slice(0, 10)} (recorded ${decl.expectUpstream.slice(0, 8)}, this TUI ${have.slice(0, 8)}): re-sync the copy (tui-slot-check --agent-brief ${decl.id})`

  // spec 09: the region is located but changed. `apply` (default) mounts the copy anyway and reports the drift; the
  // slot's runtime net (a throw shows the stock code, three throws switch it off) stays. `skip` is the old behaviour.
  return decl.onDrift === 'skip'
    ? { detail, status: 'skipped-drift' }
    : { detail: `${detail}; the copy is mounted anyway (on_drift apply)`, drifted: true, status: 'active' }
}

/** The line logged when a slot is mounted: names the slot, the extension and the upstream commit it was written against. */
export const mountLine = (decl: SlotDeclaration, extId: string): string =>
  `tui slot ${decl.id} active: extension ${extId} written against upstream ${decl.upstreamCommit.slice(0, 10)} (${decl.reason})`
