# 0.9.2.5 structural review

The task-list snapshot moves into a synchronous helper owned by the same service
so it can run off the application loop. SessionManagementService now has 26
methods (default budget 25). ConversationRuntime adds context_checkpoints for
batch reads while retaining context_checkpoint as its single-item wrapper, moving
its reviewed method count from 26 to 27. Both preserve their existing ownership.
Only these two method-count entries change. Global limits, checkers, plugin
boundaries and all unrelated budgets remain unchanged. No runtime behavior is
changed to satisfy validation.
