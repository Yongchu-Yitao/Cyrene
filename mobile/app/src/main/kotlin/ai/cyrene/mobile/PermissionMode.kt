package ai.cyrene.mobile

enum class PermissionMode(val wireValue: String) {
    AUTO("auto"),
    DEFAULT("default"),
    PLAN("plan");

    fun approvalWireValue(): String = if (this == AUTO) AUTO.wireValue else DEFAULT.wireValue

    fun taskWireValue(): String = if (this == AUTO) AUTO.wireValue else DEFAULT.wireValue

    companion object {
        fun fromWireValue(value: String?): PermissionMode = entries.firstOrNull {
            it.wireValue == value?.trim()?.lowercase()
        } ?: AUTO
    }
}
