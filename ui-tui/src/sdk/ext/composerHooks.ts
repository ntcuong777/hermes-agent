/** What the composer asks of extensions (stage 1). The only two functions core code calls for paste and clipboard image. */
import { extensionHost, type Host } from './host.js'

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
