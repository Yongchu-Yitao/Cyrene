package ai.cyrene.mobile.runtime.protocol

import org.json.JSONObject

/** Percent is for the current measured stage, never an estimate of total startup time. */
data class StartupProgress(val stage: String, val completed: Long = 0, val total: Long = 0) {
    val percent: Int? get() = if (total > 0) ((completed.coerceIn(0, total).toDouble() / total) * 100).toInt() else null
    fun toJson(): String = JSONObject().put("stage", stage).put("completed", completed).put("total", total).toString()
    companion object {
        fun parse(raw: String): StartupProgress = JSONObject(raw).let {
            StartupProgress(it.getString("stage"), it.optLong("completed"), it.optLong("total"))
        }
    }
}
