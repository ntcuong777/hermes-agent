/**
 * Seam. tui-keybinds installs the chord table and the choices picker on
 * `globalThis.__hermesKeybinds`. Absent, a chord does nothing and a choices
 * reply is not applied.
 */

type KeybindApi = {
  keybindRunner?: (ctx: unknown, onSettled: () => void) => (key: unknown, ch: string) => boolean
  setConfiguredKeybinds?: (value: unknown) => void
  typedDirectives?: (
    gw: { request: (method: string, params?: Record<string, unknown>) => Promise<unknown> },
    setInput: (text: string) => void,
    say: (text: string) => void,
    sid: string,
    stale: () => boolean
  ) => { apply: (raw: unknown) => void }
}

const live = (): KeybindApi =>
  (globalThis as { __hermesKeybinds?: KeybindApi }).__hermesKeybinds ?? {}

export const setConfiguredKeybinds = (value: unknown): void => {
  live().setConfiguredKeybinds?.(value)
}

export const keybindRunner =
  (ctx: unknown, onSettled: () => void) =>
  (key: unknown, ch: string): boolean =>
    live().keybindRunner?.(ctx, onSettled)?.(key, ch) ?? false

export const typedDirectives = (
  gw: { request: (method: string, params?: Record<string, unknown>) => Promise<unknown> },
  setInput: (text: string) => void,
  say: (text: string) => void,
  sid: string,
  stale: () => boolean
) => live().typedDirectives?.(gw, setInput, say, sid, stale) ?? { apply() {} }
