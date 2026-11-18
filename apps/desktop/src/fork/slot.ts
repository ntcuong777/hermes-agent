// fork: tui slot (spec 06b stage 8, decision D12): the ONE build-time slot exception to the plugin rule.
// Electron's renderer has no plugin runtime, so the fork copy is selected at Vite build time: the
// __HERMES_SLOT_HASHES__ define carries the region hash of the CURRENT upstream-owned source (computed by
// vite.config.ts through the same slotRegion.mjs the TUI uses). Spec 09 (plugin-first): when that hash differs
// from the one recorded with the copy, `onDrift` 'apply' (the default; VITE_HERMES_SLOT_ON_DRIFT=skip flips it
// for a build) still builds the fork copy and prints one console.warn naming both hashes; 'skip' builds
// upstream's own component (the old behaviour). A region that cannot be located (no build hash) always builds
// upstream's component. The drift is a build-time event, never a runtime failure.

export type OnDrift = 'apply' | 'skip'

const envPolicy = (): OnDrift | undefined => {
  const value = (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_HERMES_SLOT_ON_DRIFT

  return value === 'skip' || value === 'apply' ? value : undefined
}

export function forkSlot<P>(id: string, Stock: P, Fork: P, expectedHash: string, onDrift?: OnDrift): P {
  const hashes = (globalThis as { __HERMES_SLOT_HASHES__?: Record<string, string> }).__HERMES_SLOT_HASHES__
  const current = hashes?.[id]

  if (current === expectedHash) {
    return Fork
  }

  if (current !== undefined && (onDrift ?? envPolicy() ?? 'apply') === 'apply') {
    console.warn(
      `[fork slot] ${id}: upstream changed (build hash ${current.slice(0, 10)} != recorded ${expectedHash.slice(0, 10)}); the fork copy still builds (on_drift apply)`
    )

    return Fork
  }

  console.warn(
    `[fork slot] ${id}: the fork copy is skipped (build hash ${current ?? 'missing'} != recorded ${expectedHash.slice(0, 10)}); upstream's own component builds`
  )

  return Stock
}
