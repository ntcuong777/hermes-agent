// fork: build-time slot hashes for slot.ts (the one build-time slot exception, stage 8).
// vite.config.ts imports this and embeds the map via `__HERMES_SLOT_HASHES__`. Each entry
// is the region hash of the CURRENT upstream-owned source, computed with the same
// slotRegion.mjs the TUI uses; slot.ts compares it against the recorded hash at build time.
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

import { regionHash } from '../../../../ui-tui/src/sdk/ext/slotRegion.mjs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export const desktopSlotHashes = () => ({
  // No symbols (whole file): the fork copy is the whole module, so any upstream edit to this
  // file is drift a copy written against the old card must not outlive.
  approvalCard: regionHash(
    fs.readFileSync(path.resolve(__dirname, '../components/assistant-ui/tool/approval.tsx'), 'utf8')
  )
})
