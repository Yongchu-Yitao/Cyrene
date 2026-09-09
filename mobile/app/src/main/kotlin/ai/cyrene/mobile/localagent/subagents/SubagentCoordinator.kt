package ai.cyrene.mobile.localagent.subagents

import ai.cyrene.mobile.localagent.runtime.RunBudget
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import java.io.File
import java.util.UUID

enum class SubagentState { CREATED, RUNNING, WAITING, INTERRUPTING, PAUSED, COMPLETED, FAILED, CANCELLED }

data class SubagentSpec(
    val agentId: String,
    val runId: String,
    val parentAgentId: String?,
    val generation: Long,
    val depth: Int,
    val workPacket: String,
    val contextRef: String,
    val workspace: File,
    val budget: RunBudget,
    val state: SubagentState,
    val mailboxCursor: Long,
)

data class MailboxEvent(
    val eventId: String,
    val agentId: String,
    val sequence: Long,
    val type: String,
    val payload: String,
)

data class SubagentResult(
    val status: SubagentState,
    val summary: String,
    val evidenceRefs: List<String>,
    val changedPaths: List<String>,
    val unresolvedRisks: List<String>,
)

interface SubagentStore {
    suspend fun get(agentId: String): SubagentSpec?
    suspend fun list(runId: String): List<SubagentSpec>
    suspend fun save(spec: SubagentSpec)
    suspend fun append(event: MailboxEvent)
    suspend fun mailbox(agentId: String, after: Long): List<MailboxEvent>
    suspend fun result(agentId: String): SubagentResult?
    suspend fun saveResult(agentId: String, result: SubagentResult)
}

fun interface SubagentRunner {
    suspend fun run(spec: SubagentSpec, messages: List<MailboxEvent>): SubagentResult
}

class SubagentCoordinator(
    private val root: File,
    private val store: SubagentStore,
    private val runner: SubagentRunner,
    maxConcurrency: Int,
    private val maxDepth: Int,
    private val maxAgents: Int,
) {
    private val semaphore = Semaphore(maxConcurrency.coerceAtLeast(1))
    private val mutation = Mutex()
    @Volatile private var cancelled = false

    suspend fun spawn(
        runId: String,
        generation: Long,
        parentAgentId: String?,
        workPacket: String,
        contextRef: String,
        budget: RunBudget,
    ): SubagentSpec = mutation.withLock {
        check(!cancelled) { "subagent scheduling is cancelled" }
        val existing = store.list(runId)
        require(existing.size < maxAgents) { "subagent count limit reached" }
        val parent = parentAgentId?.let { store.get(it) ?: error("parent subagent does not exist") }
        val depth = (parent?.depth ?: -1) + 1
        require(depth <= maxDepth) { "subagent depth limit reached" }
        require(parent == null || parent.generation == generation) { "subagent generation mismatch" }
        val agentId = "agent_${UUID.randomUUID().toString().replace("-", "")}"
        val workspace = safeWorkspace(runId, agentId).apply { mkdirs() }
        val spec = SubagentSpec(agentId, runId, parentAgentId, generation, depth, workPacket, contextRef, workspace, budget, SubagentState.CREATED, 0)
        store.save(spec)
        store.append(MailboxEvent("mb_${UUID.randomUUID()}", agentId, 1, "subagent_spawned", workPacket))
        spec
    }

    suspend fun execute(agentId: String): SubagentResult = semaphore.withPermit {
        val spec = store.get(agentId) ?: error("subagent does not exist")
        if (cancelled) return@withPermit cancel(agentId)
        if (spec.state == SubagentState.COMPLETED) return@withPermit store.result(agentId) ?: error("completed subagent result is missing")
        val running = spec.copy(state = SubagentState.RUNNING)
        store.save(running)
        val result = try {
            runner.run(running, store.mailbox(agentId, running.mailboxCursor))
        } catch (error: Throwable) {
            SubagentResult(SubagentState.FAILED, error.message ?: "Subagent failed", emptyList(), emptyList(), listOf("runner_failure"))
        }
        val finalStatus = when (result.status) {
            SubagentState.COMPLETED, SubagentState.FAILED, SubagentState.CANCELLED -> result.status
            else -> SubagentState.FAILED
        }
        val normalized = result.copy(status = finalStatus)
        store.saveResult(agentId, normalized)
        store.save(running.copy(state = finalStatus))
        store.append(MailboxEvent("mb_${UUID.randomUUID()}", agentId, running.mailboxCursor + 1, "subagent_${finalStatus.name.lowercase()}", normalized.summary))
        normalized
    }

    suspend fun followUp(agentId: String, message: String) = send(agentId, "follow_up", message)
    suspend fun message(agentId: String, message: String) = send(agentId, "message", message)

    suspend fun interrupt(agentId: String) {
        val spec = store.get(agentId) ?: return
        if (spec.state !in setOf(SubagentState.COMPLETED, SubagentState.FAILED, SubagentState.CANCELLED)) {
            store.save(spec.copy(state = SubagentState.INTERRUPTING))
            send(agentId, "interrupt", "parent_requested_interrupt")
        }
    }

    suspend fun resume(agentId: String): SubagentResult {
        val spec = store.get(agentId) ?: error("subagent does not exist")
        require(spec.state in setOf(SubagentState.WAITING, SubagentState.PAUSED, SubagentState.INTERRUPTING, SubagentState.FAILED))
        store.save(spec.copy(state = SubagentState.CREATED))
        return execute(agentId)
    }

    suspend fun wait(agentId: String, after: Long): List<MailboxEvent> = store.mailbox(agentId, after)

    suspend fun cancelAll(runId: String) {
        cancelled = true
        store.list(runId).forEach { cancel(it.agentId) }
    }

    private suspend fun cancel(agentId: String): SubagentResult {
        val spec = store.get(agentId) ?: error("subagent does not exist")
        val result = SubagentResult(SubagentState.CANCELLED, "Cancelled by parent run", emptyList(), emptyList(), emptyList())
        store.save(spec.copy(state = SubagentState.CANCELLED))
        store.saveResult(agentId, result)
        return result
    }

    private suspend fun send(agentId: String, type: String, payload: String) {
        val spec = store.get(agentId) ?: error("subagent does not exist")
        val sequence = (store.mailbox(agentId, 0).maxOfOrNull(MailboxEvent::sequence) ?: 0) + 1
        store.append(MailboxEvent("mb_${UUID.randomUUID()}", agentId, sequence, type, payload))
    }

    private fun safeWorkspace(runId: String, agentId: String): File {
        require(runId.matches(Regex("[A-Za-z0-9_-]{3,180}")))
        require(agentId.matches(Regex("[A-Za-z0-9_-]{3,180}")))
        val sessionRoot = File(root, "subagents/$runId").canonicalFile
        val workspace = File(sessionRoot, agentId).canonicalFile
        require(workspace.toPath().startsWith(sessionRoot.toPath()))
        return workspace
    }
}
