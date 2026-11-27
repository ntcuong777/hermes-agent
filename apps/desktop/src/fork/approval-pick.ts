/** fork: the approvalCard slot pick (stage 8 build-time slot; decision D12 in fork/slot.ts). */
import { PendingApprovalStack as StockPendingApprovalStack } from '@/components/assistant-ui/tool/approval'
import { PendingApprovalStack as ForkPendingApprovalStack } from '@/fork/approval.fork'
import { forkSlot } from '@/fork/slot'

export const PendingApprovalStack = forkSlot(
  'approvalCard',
  StockPendingApprovalStack,
  ForkPendingApprovalStack,
  '4289e6bf2e124f2a41d7120ccb27b9b3ac500d1867d31aadf481536d29121daa',
  'skip' // rule 6.9: the approval card never runs a copy written against a changed upstream card
)
