/** `/extensions` (list, info, enable, disable, errors, reload) and `/ext <command> [args]`. Returns lines; the slash command prints them. */
import { SDK_VERSION } from './contract.js'
import { type Host } from './host.js'
import type { Entry, SlotRow } from './runtime.js'
import { type LoadReport } from './loader.js'

const row = (entry: Entry): string => {
  const hooks = [...entry.hooks.values()].reduce((sum, list) => sum + list.length, 0)
  const extra = entry.detail ? `  ${entry.detail}` : ''

  return `${entry.status.padEnd(11)} ${entry.id}  sdk ${entry.listing.sdk}  hooks ${hooks}  commands ${entry.commands.size}${extra}`
}

const slotLine = (row: SlotRow): string =>
  `${(row.drifted ? `${row.status} DRIFTED` : row.status).padEnd(22)} ${row.id}  ${row.kind}  ${row.extId}  ${row.snapshotKind === 'fork-core' ? 'fork-core' : 'upstream'} ${row.upstreamCommit.slice(0, 10) || '-'}${row.detail ? `  ${row.detail}` : ''}`

const reportLines = (reports: readonly LoadReport[]): string[] =>
  reports.length
    ? reports.map(report => `${report.status.padEnd(11)} ${report.id}${report.detail ? `  ${report.detail}` : ''}`)
    : ['nothing listed']

export const extensionsHelp = (): string[] => [
  `TUI extensions (SDK ${SDK_VERSION})`,
  '/extensions                 list every extension and its state',
  '/extensions info <id>       what it declared and may call',
  '/extensions slots           every slot an extension replaces: status, upstream commit it was copied from, why not mounted',
  '/extensions errors [id]     the last errors of one (or every) extension',
  '/extensions enable <id>     clear a disable (also after the automatic one) and load it; <id> may be a slot id',
  '/extensions disable <id>    stop it now and across restarts; <id> may be a slot id (upstream runs)',
  '/extensions reload [id]     re-list and re-load (use after editing an extension file)',
  '/ext <command> [args]       run a command an extension registered'
]

export const runExtensionsCommand = async (host: Host, arg: string): Promise<string[]> => {
  const [sub = 'list', id = ''] = arg.trim().split(/\s+/)
  const runtime = host.runtime()

  if (!runtime) {
    return [`TUI extensions are off: ${host.idleReason()}`]
  }

  switch (sub) {
    case 'list':
    case '': {
      const rows = runtime.entries().map(row)

      return [...(rows.length ? rows : ['no extensions']), ...(host.idleReason() ? [`(${host.idleReason()})`] : [])]
    }

    case 'info': {
      const entry = runtime.get(id)

      return entry
        ? [
            row(entry),
            `title: ${entry.listing.title}`,
            `file: ${entry.listing.entry}`,
            `sha256: ${entry.listing.sha256}`,
            `rpc: plugin.${entry.listing.plugin}.*${entry.listing.rpc.length ? `, ${entry.listing.rpc.join(', ')}` : ''}`,
            ...runtime
              .slotRows()
              .filter(slot => slot.extId === entry.id)
              .map(slot => `slot: ${slotLine(slot)}  (${slot.reason})`)
          ]
        : [`no extension ${id || '(give an id)'}`]
    }

    case 'slots': {
      const rows = runtime.slotRows().map(slotLine)

      return rows.length ? rows : ['no slots declared by any extension']
    }

    case 'errors': {
      const picked = id ? [runtime.get(id)].flatMap(entry => entry ?? []) : runtime.entries()
      const lines = picked.flatMap(entry => entry.errors.map(line => `${entry.id} ${line}`))

      return lines.length ? lines : ['no errors recorded']
    }

    case 'enable':
      return id ? reportLines(await host.enable(id)) : ['usage: /extensions enable <id>']

    case 'disable':
      return id ? [host.disable(id)] : ['usage: /extensions disable <id>']

    case 'reload':
      return reportLines(await host.reload(id || undefined))

    default:
      return extensionsHelp()
  }
}

export const runExtCommand = async (host: Host, arg: string): Promise<string[]> => {
  const [name = '', ...rest] = arg.trim().split(/\s+/)
  const runtime = host.runtime()

  if (!name) {
    const lines = (runtime?.entries() ?? []).flatMap(entry =>
      [...entry.commands].map(([cmd, def]) => `/ext ${cmd}  ${def.help}  (${entry.id})`)
    )

    return lines.length ? lines : ['no extension registered a command']
  }

  const printed: string[] = []
  const found = await host.runCommand(name, rest.join(' '), text => void printed.push(text))

  return found ? printed : [`unknown extension command '${name}' (/ext lists them)`]
}
