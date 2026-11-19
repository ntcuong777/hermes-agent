/** fork: the MCP context rides the canonical approval prompt params (canonical-protocol.ts's
 *  one-line carrier). Upstream's canonical-protocol.test.ts stays upstream's own. */
import { expect, test } from 'vitest'

import { canonicalPromptParams } from './canonical-protocol'

test('an approval card keeps its MCP tool-call context on live events and replayed snapshots', () => {
  const mcp = { server: 'srv', tool: 'write', tool_description: 'writes', arguments: '{}' }
  const prompt = { prompt_id: 'p', command: 'c', description: 'd', choices: ['once', 'deny'], mcp }

  expect(canonicalPromptParams('approval', 's', prompt)).toMatchObject({ request_id: 'p', mcp })
  // No MCP context stays absent, never an invented empty object.
  expect(canonicalPromptParams('approval', 's', { prompt_id: 'p' }).mcp).toBeUndefined()
})
