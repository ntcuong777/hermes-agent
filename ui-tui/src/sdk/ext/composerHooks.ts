/** What the composer asks of extensions. Paste, clipboard image, and the external editor. */
import { extensionHost, type Host } from './host.js'

export type ComposerEditorPlan =
  | { text: string }
  | { buf: string[]; draft: true; handled: true; line: string }
  | { handled: true; joined: string; kind: 'collapse'; text: string }
  | { draft?: { buf: string[]; line: string }; handled: true; kind: 'stash'; tokenText?: string }

type ComposerEditorPayload = {
  collapseChars?: number
  collapseLines?: number
  destinationOk?: boolean
  joined?: string
  phase: 'prepare' | 'saved'
  saved?: string
  text?: string
  tokens?: ReadonlyArray<{ kind: string; label?: string; text?: string }>
}

type ComposerEditorFn = (payload: ComposerEditorPayload) => ComposerEditorPlan | null | undefined

let composerEditorFn: ComposerEditorFn | null = null

/** tui-ports registers the editor that expands pastes and does not send on save. */
export const registerComposerEditor = (fn: ComposerEditorFn): void => {
  composerEditorFn = fn
}

export const hasComposerEditor = (): boolean => composerEditorFn != null

export const composerEditor = (payload: ComposerEditorPayload): ComposerEditorPlan | null =>
  composerEditorFn?.(payload) ?? null

const editorRegistry = globalThis as { __hermesRegisterComposerEditor?: typeof registerComposerEditor }

editorRegistry.__hermesRegisterComposerEditor = registerComposerEditor

/**
 * `composer.paste`: the text to insert after every extension had its say, or `null` when one swallowed the paste.
 * Only call it when `host.has('composer.paste')`: with no handler it would add an await to every paste.
 */
export const transformPasteText = async (
  text: string,
  bracketed: boolean,
  host: Host = extensionHost
): Promise<null | string> => {
  const result = (await host.runHook('composer.paste', { bracketed, text })) as null | { text?: unknown }

  return result === null ? null : typeof result?.text === 'string' ? result.text : text
}

/** `composer.clipboardImage`: the path of a PNG an extension staged from the system clipboard, or null. The core attaches it like a dropped file. */
export const clipboardImagePathFromExtensions = async (host: Host = extensionHost): Promise<null | string> => {
  const path = await host.runHook('composer.clipboardImage', {})

  return typeof path === 'string' && path.length > 0 ? path : null
}
