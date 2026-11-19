/**
 * The last listing the host plugin gave, kept at `<home>/tui-extensions-cache.json`, so the NEXT start can mount slot
 * extensions before the first frame without waiting for the gateway. The gateway stays the authority: when it
 * answers, a revoked grant, a changed file or a removed extension is applied at once. A cached row is checked like a
 * live one (location, sha256 of the file), so a stale cache can only fail to load, never load something else.
 */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

import { type Listing, LISTING_VERSION } from './contract.js'
import { defaultStatePath } from './store.js'

export const defaultCachePath = (env: NodeJS.ProcessEnv = process.env): string =>
  join(dirname(defaultStatePath(env)), 'tui-extensions-cache.json')

/** `parse` is the loader's row parser; it accepts the cached (camelCase) slot fields as well as the wire ones. */
export const readListingCache = (path: string, parse: (raw: unknown) => unknown): Listing | null => {
  try {
    const data = JSON.parse(readFileSync(path, 'utf8')) as { extensions?: unknown; version?: unknown }

    return data.version === LISTING_VERSION && Array.isArray(data.extensions)
      ? {
          extensions: data.extensions.flatMap(row => (parse(row) as Listing['extensions'][number] | null) ?? []),
          version: LISTING_VERSION
        }
      : null
  } catch {
    return null
  }
}

export const writeListingCache = (path: string, listing: Listing): void => {
  try {
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(`${path}.tmp`, `${JSON.stringify(listing)}\n`)
    renameSync(`${path}.tmp`, path)
  } catch {
    // read-only home: the next start simply has no early load
  }
}
