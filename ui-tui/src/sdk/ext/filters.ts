/**
 * Synchronous value filters: the core passes a value it computed through `filterValue(name, value, ctx)`, and a TUI
 * extension may return a replacement. Unlike a hook this is synchronous (a render or a reducer cannot await), and
 * unlike a slot there is no copy of core text to keep in step: the extension sees the core's own value and the
 * inputs it was computed from.
 *
 * Fail-open: no filter registered returns `value` untouched, and a filter that throws, or returns `undefined`, is
 * skipped. An extension registers with `globalThis.__hermesRegisterFilter(name, (value, ctx) => value')`.
 */
type Filter = (value: any, ctx: any) => unknown

const filters = new Map<string, Filter[]>()

export const registerFilter = (name: string, filter: Filter): (() => void) => {
  const list = filters.get(name) ?? []

  list.push(filter)
  filters.set(name, list)

  return () => {
    const at = list.indexOf(filter)

    if (at >= 0) {
      list.splice(at, 1)
    }
  }
}

export const filterValue = <T>(name: string, value: T, ctx?: unknown): T => {
  const list = filters.get(name)

  if (!list?.length) {
    return value
  }

  let out = value

  for (const filter of list) {
    try {
      const next = filter(out, ctx)

      if (next !== undefined) {
        out = next as T
      }
    } catch {
      // a broken filter must not take the core's own answer with it
    }
  }

  return out
}

const registry = globalThis as { __hermesRegisterFilter?: typeof registerFilter }

registry.__hermesRegisterFilter = registerFilter
