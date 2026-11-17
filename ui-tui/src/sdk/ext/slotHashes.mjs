/**
 * esbuild plugin of scripts/build/tui.mjs: replaces `sdk/ext/upstreamHashes.ts` with the region hash of every slot
 * in slots.json, computed from the files being bundled. A file or symbol that cannot be read leaves that slot
 * without a hash (the host then refuses the slot); the build never fails because of it.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

import { regionHash } from './slotRegion.mjs'

export const slotHashTable = (repoRoot, slotsJsonPath) => {
  const slots = JSON.parse(readFileSync(slotsJsonPath, 'utf8')).slots ?? {}

  return Object.fromEntries(
    Object.entries(slots).flatMap(([id, def]) => {
      try {
        return [[id, regionHash(readFileSync(join(repoRoot, def.file), 'utf8'), def.symbols)]]
      } catch {
        return []
      }
    })
  )
}

export const slotHashesPlugin = repoRoot => ({
  name: 'hermes-slot-hashes',
  setup(build) {
    build.onLoad({ filter: /sdk[\\/]ext[\\/]upstreamHashes\.ts$/ }, args => ({
      contents: `export const UPSTREAM_HASHES: Readonly<Record<string, string>> = ${JSON.stringify(
        slotHashTable(repoRoot, join(dirname(args.path), 'slots.json'))
      )}\n`,
      loader: 'ts'
    }))
  }
})
