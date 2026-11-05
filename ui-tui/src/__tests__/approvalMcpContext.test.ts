import { describe, expect, it } from 'vitest'

import { approvalMcpContext } from '../app/createServerRequestHandler.js'
import { approvalMcpBody } from '../components/prompts.js'
import type { ApprovalMcpContext } from '../types.js'

const ctx: ApprovalMcpContext = {
  server: 'linear',
  tool: 'create_issue',
  tool_description: 'Create an issue in a repository',
  arguments: '{\n  "title": "x"\n}'
}

describe('approval MCP context extraction', () => {
  it('keeps a well-formed context and normalizes empty pieces to null', () => {
    expect(approvalMcpContext({ server: ' linear ', tool: 'create_issue', tool_description: '  ', arguments: '' }))
      .toEqual({ server: 'linear', tool: 'create_issue', tool_description: null, arguments: null })
  })

  it('drops a context that does not name both server and tool', () => {
    expect(approvalMcpContext({ server: 'linear' })).toBeUndefined()
    expect(approvalMcpContext({ tool: 'create_issue' })).toBeUndefined()
    expect(approvalMcpContext('nope')).toBeUndefined()
    expect(approvalMcpContext(undefined)).toBeUndefined()
  })

  it('ignores non-string fields instead of echoing them', () => {
    expect(approvalMcpContext({ server: 's', tool: 't', arguments: 42 })).toEqual({
      server: 's',
      tool: 't',
      tool_description: null,
      arguments: null
    })
  })
})

describe('approval MCP body (the panel a tool call renders)', () => {
  it('leads with why + what the tool does, then a labelled arguments block', () => {
    expect(approvalMcpBody(ctx, "Server 'linear' is configured 'trust: untrusted'.", 'Arguments:')).toEqual([
      "Server 'linear' is configured 'trust: untrusted'.",
      'Create an issue in a repository',
      'Arguments:',
      '{',
      '  "title": "x"',
      '}'
    ])
  })

  it('omits the label when the call has no arguments', () => {
    expect(approvalMcpBody({ ...ctx, arguments: null }, 'why', 'Arguments:')).toEqual(['why', 'Create an issue in a repository'])
  })

  it('omits an absent tool description without leaving blank lines', () => {
    expect(approvalMcpBody({ server: 's', tool: 't', tool_description: null, arguments: '{}' }, 'why', 'Args:')).toEqual([
      'why',
      'Args:',
      '{}'
    ])
  })
})
