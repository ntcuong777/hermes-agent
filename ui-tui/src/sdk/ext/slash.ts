/** The two TUI slash commands of the extension host (`/extensions`, `/ext`). Registered by one spread in app/slash/registry.ts. */
import type { SlashCommand } from '../../app/slash/types.js'

import { runExtCommand, runExtensionsCommand } from './command.js'
import { extensionHost } from './host.js'

export const extensionCommands: SlashCommand[] = [
  {
    help: 'list, enable, disable and reload TUI extensions; show their errors',
    name: 'extensions',
    run: (arg, ctx) =>
      void runExtensionsCommand(extensionHost, arg).then(lines => lines.forEach(line => ctx.transcript.sys(line))),
    usage: '/extensions [list|info|slots|errors|enable|disable|reload] [id]'
  },
  {
    help: 'run a command a TUI extension registered',
    name: 'ext',
    run: (arg, ctx) =>
      void runExtCommand(extensionHost, arg).then(lines => lines.forEach(line => ctx.transcript.sys(line))),
    usage: '/ext <command> [args]'
  }
]
