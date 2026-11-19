/** fork: the plugin-dispatch member of the dispatch union (the parser seam plugins register on).
 *  Upstream's slash.test.ts stays upstream's own; the payload shapes and their handling belong to the
 *  registering plugins (tui-ports owns the stash picker's choices and composer-clear). */
import { describe, expect, it } from 'vitest'

import { parseCommandDispatch, registerCommandDispatchParser, type CommandDispatchParser } from './slash'

describe('the plugin-dispatch member', () => {
  it('routes an unknown type through a registered parser and wraps the row as the plugin owns it', () => {
    const parser: CommandDispatchParser = row =>
      row.type === 'test-fork-directive' && typeof row.title === 'string'
        ? { payload: row, type: 'plugin-dispatch' }
        : null
    registerCommandDispatchParser(parser)

    const row = { title: 'x', type: 'test-fork-directive' }
    expect(parseCommandDispatch(row)).toEqual({ payload: row, type: 'plugin-dispatch' })
    expect(parseCommandDispatch({ type: 'test-fork-directive' })).toBeNull()
  })

  it('leaves an unknown type no parser claimed as null', () => {
    expect(parseCommandDispatch({ type: 'nope' })).toBeNull()
    expect(parseCommandDispatch({ type: 'stash-saved' })).toBeNull()
  })
})
