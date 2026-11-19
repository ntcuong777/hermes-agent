/**
 * Fork command.dispatch shapes. `slash.ts` calls this for types upstream does not know.
 * `choices` opens a picker. `composer-clear` empties the composer after a keybind save.
 */
import type { ChoicesCommandDispatchResponse, CommandDispatchResponse } from './slash.js'

const str = (value: unknown) => (typeof value === 'string' ? value : undefined)

const choiceItems = (value: unknown): ChoicesCommandDispatchResponse['items'] | null => {
  const rows = Array.isArray(value) ? (value as Record<string, unknown>[]) : []

  return rows.length && rows.every(r => r && typeof r.label === 'string' && typeof r.select === 'string')
    ? rows.map(r => ({ drop: str(r.drop), label: r.label as string, select: r.select as string }))
    : null
}

export function parseForkCommandDispatch(row: Record<string, unknown>): CommandDispatchResponse | null {
  switch (row.type) {
    case 'choices': {
      const items = choiceItems(row.items)

      return items && typeof row.title === 'string'
        ? {
            items,
            notice: str(row.notice),
            selected: typeof row.selected === 'number' ? row.selected : undefined,
            title: row.title,
            type: 'choices'
          }
        : null
    }

    case 'composer-clear':
      return { notice: str(row.notice), type: 'composer-clear' }

    default:
      return null
  }
}

/** Optional command to run after a prefill lands in the composer. Absent on upstream payloads. */
export function forkPrefillAck(row: Record<string, unknown>): string | undefined {
  return str(row.ack)
}
