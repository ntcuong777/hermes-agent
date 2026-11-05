// Overlays, pickers and dialogs: prompts, model/pet pickers, hubs, agents panel,
// billing/subscription, connection setup. Owned namespaces: `overlay`, `prompt`,
// `picker`, `hub`, `agents`, `billing`, `connection`, `journey`.

export const overlaysEn = {
  prompt: {
    approval: {
      title: '⚠ approval required',
      // "above" was never true: the tool row above is a one-line preview. execute_code names a file with the whole
      // script on this panel's first line (tools/approval.py, P21).
      moreLines: (count: number) => `… +${count} more line${count === 1 ? '' : 's'} (press v for the full text)`,
      // MCP tool-call approvals carry structured context (server/tool/args); the panel titles
      // itself with the call instead of the generic dangerous-command header.
      mcpTitle: (server: string, tool: string) => `MCP ${server}/${tool}`,
      arguments: 'Arguments:',
      once: 'Allow once',
      session: 'Allow this session',
      always: 'Always allow',
      deny: 'Deny'
    },
    clarify: {
      other: 'Other (type your answer)',
      skipped: '(skipped)',
      toggle: 'Space toggle',
      confirmAndContinue: 'confirm and continue',
      lockAnswer: 'lock answer',
      typingHint: (enterAction: string) => `Enter ${enterAction} · Esc back`,
      hint: (enterAction: string) =>
        `↑/↓ select · Enter ${enterAction} · Tab/Shift+Tab switch question · Esc/Ctrl+C cancel`
    },
    confirm: {
      confirm: 'Yes',
      cancel: 'No',
      hint: '↑/↓ select · Enter confirm · Y/N quick · Esc cancel'
    }
  }
}
