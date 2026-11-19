/** Slash commands a TUI extension offers in place of a built-in one. The registry asks here before its own table. */
import type { SlashCommand } from '../../app/slash/types.js'

const overrides = new Map<string, SlashCommand>()

/** An extension registers a command under its name and aliases; a later registration of the same name wins. */
export const registerSlashOverride = (command: SlashCommand): void => {
  for (const name of [command.name, ...(command.aliases ?? [])]) {
    overrides.set(name.toLowerCase(), command)
  }
}

export const slashOverride = (name: string): SlashCommand | undefined => overrides.get(name)

const registry = globalThis as { __hermesRegisterSlashOverride?: typeof registerSlashOverride }

registry.__hermesRegisterSlashOverride = registerSlashOverride
