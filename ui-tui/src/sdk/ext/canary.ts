/** The Stage 0 canary: a fork-owned function behind a slot, so the whole slot path is testable before any real slot exists. */
import { extensionHost } from './host.js'
import { slotFn } from './slotReact.js'

export const canaryEcho = (text: string): string => text

/** What the app would call: the extension's `canary.echo` when one is mounted, else `canaryEcho`. */
export const echo = (text: string): string => slotFn('canary.echo', canaryEcho, extensionHost)(text)
