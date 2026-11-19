# apps/desktop/src/fork/ (spec 06b stage 8)

FLAG TO THE PERSON: this directory is the ONE exception to the plugins rule (decision D12, accepted): Electron
has no plugin runtime, so the Desktop approval card slot is selected at BUILD time from fork-owned files here,
not from a nix-darwin plugin. On a hash mismatch the build selects upstream's component and prints one
console.warn; there is no runtime notice and no /extensions entry (the TUI side keeps those).

Contents:
- `slot.ts`: `forkSlot(id, Stock, Fork, expectedHash)` - the build-time selector.
- `approval.fork.tsx`: the fork's `apps/desktop/src/components/assistant-ui/tool/approval.tsx` (the
  MCP header, tool description and full arguments on the approval card; rule 6.9). Its `@/` imports resolve
  unchanged from this directory; the three sibling imports (`./approval-activity`, `./fallback-model/targets`,
  `./run-summary`) are rewritten to the `@/components/assistant-ui/tool/...` alias because the copy lives in a
  different directory (the only edit to the copy; the slot lock hashes upstream's region, not this file).
- `slots.json`, `tui_slots.lock.json`, `upstream-source/approvalCard.tsx.txt`: provenance, recorded with
  `tui-slot-check --record approvalCard --plugin-dir apps/desktop/src/fork --fork-root <fork>`.

Fallback: upstream's card (an MCP call shows its command text only, arguments cut). The full-command paging
still shows every line, so nothing is approved unseen; what is lost is the structured MCP view.
Re-sync: `tui-slot-check --agent-brief approvalCard --plugins-root <fork>/apps/desktop/src/fork --fork-root <fork>`.
