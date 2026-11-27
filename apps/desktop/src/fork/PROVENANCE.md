# apps/desktop/src/fork/ (spec 06b stage 8)

FLAG TO THE PERSON: this directory is the ONE exception to the plugins rule (decision D12, accepted): Electron
has no plugin runtime, so the Desktop approval card slot is selected at BUILD time from fork-owned files here,
not from a nix-darwin plugin. On a hash mismatch the build selects upstream's component and prints one
console.warn; there is no runtime notice and no /extensions entry (the TUI side keeps those).

Contents:
- `slot.ts`: `forkSlot(id, Stock, Fork, expectedHash)` - the build-time selector.
- `approval-pick.ts`: the pick. `PendingApprovalStack` is the fork copy while the build hash of upstream's
  `approval.tsx` equals the recorded one; the policy is `skip` (rule 6.9: the approval card never runs a copy
  written against a changed upstream card). `components/assistant-ui/thread/list.tsx` imports it.
- `approval.fork.tsx`: a whole-file copy of `apps/desktop/src/components/assistant-ui/tool/approval.tsx`
  (rule 6.9) built by `scripts/fork/desktop-approval-card-copy`, never edited by hand. The fork's edits are
  four: the three sibling imports (`./approval-activity`, `./fallback-model/targets`, `./run-summary`) are
  rewritten to the `@/components/assistant-ui/tool/...` alias because the copy lives in a different directory,
  and `CommandApprovalCard` shows an MCP tool call's server · tool header, tool description and full
  arguments (the strings are the copy's own: upstream's Translations has no such keys).
- `approval-mcp.ts`: `parseApprovalMcp(v)` reads an approval's MCP context (contract `ApprovalMcpContext`).
  Its carriers are fork lines in upstream files: `mcp: parseApprovalMcp(p.mcp)` in
  `app/session/hooks/use-message-stream/gateway-event/approval-request.ts` (the `approval` server request; it
  sat in `server-requests.ts` until upstream moved the handler in the 0.23.4 sync) and in `store/prompts.ts`
  (the type field, the raw field, and the restore of a pending approval).
- `composer-prefill.ts`: composer prefill routing, carried by `requestComposerPrefill(...)` lines in
  `app/session/hooks/use-prompt-actions/slash.ts` and `slash-session-commands.ts`.
- `slots.json`, `tui_slots.lock.json`, `upstream-source/approvalCard.tsx.txt`: provenance, recorded with
  `tui-slot-check --record approvalCard --plugin-dir apps/desktop/src/fork --fork-root <fork>`.

The lock covers the WHOLE upstream file (`symbols: []`, `sha256-lf`): the pick replaces `PendingApprovalStack`,
so every helper the copy renders is the copy's own, and any upstream edit to the file is a change the copy was
not written against. The earlier lock hashed `ApprovalCard`'s region alone, which upstream's folder and site
cards left untouched while the card moved under it.

Guards: `tests/tui_slots/test_desktop_fork_carriers.py` (a fork import nothing uses, a carrier file that is
not its upstream blob plus the fork lines, the pick and the lock disagreeing, a stale copy or lock);
`fork-check.yml` runs it and verifies the lock.

Fallback: upstream's card (an MCP call shows its command text only, arguments cut). The full-command paging
still shows every line, so nothing is approved unseen; what is lost is the structured MCP view.
Re-sync: run `scripts/fork/desktop-approval-card-copy` (it stops when an anchor moved: re-port that edit in
the script), then `tui-slot-check --record approvalCard --plugin-dir apps/desktop/src/fork --fork-root <fork>
--reason "..."`, put the printed hash into `approval-pick.ts`, and re-pin the carriers the test names.
