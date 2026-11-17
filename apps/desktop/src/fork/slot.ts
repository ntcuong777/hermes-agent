// fork: tui slot (spec 06b stage 8, decision D12): the ONE build-time slot exception to the plugin rule.
// Electron's renderer has no plugin runtime, so the fork copy is selected at Vite build time: the
// __HERMES_SLOT_HASHES__ define carries the region hash of the CURRENT upstream-owned source (computed by
// vite.config.ts through the same slotRegion.mjs the TUI uses); forkSlot builds the fork copy only when it
// still matches the hash recorded when the copy was made, else upstream's own component builds with one
// console.warn (the drift is a build-time event, never a runtime failure).

export function forkSlot<P>(id: string, Stock: P, Fork: P, expectedHash: string): P {
  const hashes = (globalThis as { __HERMES_SLOT_HASHES__?: Record<string, string> }).__HERMES_SLOT_HASHES__
  const current = hashes?.[id]

  if (current === expectedHash) {
    return Fork
  }

  console.warn(
    `[fork slot] ${id}: the fork copy is skipped (build hash ${current ?? 'missing'} != recorded ${expectedHash.slice(0, 10)}); upstream's own component builds`
  )

  return Stock
}
