/** fork: the approvalCard slot pick (stage 8 build-time slot; decision D12 in fork/slot.ts). */
import { PendingApprovalStack as StockPendingApprovalStack } from '@/components/assistant-ui/tool/approval'
import { PendingApprovalStack as ForkPendingApprovalStack } from '@/fork/approval.fork'
import { forkSlot } from '@/fork/slot'

export const PendingApprovalStack = forkSlot(
  'approvalCard',
  StockPendingApprovalStack,
  ForkPendingApprovalStack,
  '3b85239a1ace096a3d7a67dae9beb246a32029002e1ec800f0bf97e7b9845bd3',
  'skip' // rule 6.9: the approval card never runs a copy written against a changed upstream card
)
