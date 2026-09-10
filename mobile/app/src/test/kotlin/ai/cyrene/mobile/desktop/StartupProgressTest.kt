package ai.cyrene.mobile.desktop

import ai.cyrene.mobile.runtime.protocol.StartupProgress
import org.junit.Assert.*
import org.junit.Test

class StartupProgressTest {
    @Test fun unmeasuredStagesDoNotInventPercentages() {
        assertNull(StartupProgress("boot").percent)
        assertNull(StartupProgress("backend", 1, -1).percent)
    }
    @Test fun handlesLargeDisksAndBounds() {
        val size = 8L * 1024 * 1024 * 1024
        assertEquals(50, StartupProgress("disk", size / 2, size).percent)
        assertEquals(100, StartupProgress("disk", size + 1, size).percent)
        assertEquals(0, StartupProgress("disk", -1, size).percent)
    }
    @Test fun retainsStageAndByteCountsAcrossBinderJson() {
        val progress = StartupProgress("unpack", 4_000_000_000L, 8_589_934_592L)
        assertEquals(progress, StartupProgress.parse(progress.toJson()))
    }
}
