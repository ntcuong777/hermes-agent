/** fork: structured MCP tool-call context on an approval (contract `ApprovalMcpContext`).
 *  The fork approval card renders server/tool/what-it-does/arguments instead of command
 *  chrome; the parser lives here (fork-owned) and the approval store carries the parsed
 *  value through its `ApprovalRequest` type. Upstream's store has no such field, so with
 *  the fork card absent the context is simply unused -- the safe fallback. */

/** Structured MCP tool-call context on an approval (contract `ApprovalMcpContext`). */
export interface ApprovalMcpContext {
  server: string
  tool: string
  toolDescription: string | null
  arguments: string | null
}

/** MCP context off a wire payload; undefined unless it names both server and tool. */
export function parseApprovalMcp(v: unknown): ApprovalMcpContext | undefined {
  if (!v || typeof v !== 'object') {
    return undefined
  }

  const raw = v as Record<string, unknown>
  const str = (x: unknown): string => (typeof x === 'string' ? x.trim() : '')
  const server = str(raw.server)
  const tool = str(raw.tool)

  if (!server || !tool) {
    return undefined
  }

  return {
    server,
    tool,
    toolDescription: str(raw.tool_description) || null,
    arguments: str(raw.arguments) || null
  }
}
