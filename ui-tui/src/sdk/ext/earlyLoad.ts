/**
 * Before the first frame (entry.tsx, one await): attach the host and mount the slot extensions of the cached listing,
 * within a budget. A slow or failing extension never delays the TUI past the budget and never stops it: the load
 * carries on in the background and its slots mount when ready (components and functions swap live; a hook slot
 * waits for the next start). Notices raised here are held until the UI attaches.
 */
import { buildHostDeps } from './hostDeps.js'
import { extensionHost, extensionsDisabledByEnv, type Host } from './host.js'
import type { GatewayLike } from './api.js'

export const EARLY_BUDGET_MS = 1500

export const primeExtensionSlots = async (
  gw: GatewayLike,
  host: Host = extensionHost,
  budgetMs: number = EARLY_BUDGET_MS,
  depsFor: typeof buildHostDeps = buildHostDeps
): Promise<void> => {
  if (extensionsDisabledByEnv()) {
    return
  }

  try {
    host.attach({ ...depsFor(gw, () => {}), deferNotices: true })

    let timer: ReturnType<typeof setTimeout> | undefined

    await Promise.race([
      host.preload(),
      new Promise<void>(resolve => {
        timer = setTimeout(resolve, budgetMs)
        timer.unref?.()
      })
    ])
    clearTimeout(timer)
  } catch {
    // an extension problem is never the TUI's problem
  }
}
