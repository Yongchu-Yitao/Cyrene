package ai.cyrene.mobile

import android.net.Uri

/** Attachment metadata shared with the retained local-agent data layer. */
data class PendingAttachment(
    val uri: Uri,
    val name: String,
    val size: Long,
)
