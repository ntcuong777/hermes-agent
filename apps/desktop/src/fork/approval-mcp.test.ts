/** fork: the approval-card MCP context carrier and its parser (fork/approval-mcp.ts), plus the
 *  `approval.pending` replay path through the approval store. Moved out of upstream's
 *  store/prompts.test.ts, which stays upstream's own. */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { parseApprovalMcp } from '@/fork/approval-mcp'
import {
  $approvalRequest,
  clearApprovalRequest,
  replayPendingApproval,
  sessionApprovalRequests,
  setApprovalRequest
} from '@/store/prompts'

describe('parseApprovalMcp', () => {
  it('parses the structured MCP context; missing server or tool drops it', () => {
    expect(
      parseApprovalMcp({ server: 'linear', tool: 'create_issue', tool_description: ' Create ', arguments: '{}' })
    ).toEqual({ server: 'linear', tool: 'create_issue', toolDescription: 'Create', arguments: '{}' })
    expect(parseApprovalMcp({ server: 'linear' })).toBeUndefined()
    expect(parseApprovalMcp({ tool: 'create_issue', tool_description: 'd' })).toBeUndefined()
    expect(parseApprovalMcp(null)).toBeUndefined()
  })
})

describe('the approval store carries the parsed MCP context', () => {
  beforeEach(() => {
    clearApprovalRequest('s1')
  })

  it('keeps a parked MCP context on the card and through a pending replay', async () => {
    const mcp = { server: 'linear', tool: 'create_issue', toolDescription: 'Create an issue', arguments: '{}' }
    setApprovalRequest({ command: 'call', description: 'd', mcp, requestId: 'r1', sessionId: 's1' })
    expect($approvalRequest.get()?.mcp).toEqual(mcp)

    // The `approval.pending` restore path feeds the same parser.
    const gateway = {
      request: async () => ({
        approvals: [
          { request_id: 'r2', command: 'call', description: 'd', mcp: { server: 's', tool: 't', arguments: '[]' } }
        ]
      })
    }

    await replayPendingApproval(gateway as never, 's1')
    const queued = sessionApprovalRequests('s1').get().find(item => item.requestId === 'r2')
    expect(queued?.mcp).toEqual({ server: 's', tool: 't', toolDescription: null, arguments: '[]' })
    vi.restoreAllMocks()
  })
})
