package ai.cyrene.mobile.runtime

import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files

class CheckpointStoreTest {
    @Test fun consumedCheckpointCannotBeUsedAfterCrash() {
        val dir = Files.createTempDirectory("resume-test").toFile()
        try {
            val store = CheckpointStore(dir.resolve("resume"))
            val state = CheckpointStore.Saved("version-one", "a".repeat(43))
            store.publish(state)
            assertEquals(state, store.consume("version-one"))
            assertNull(CheckpointStore(dir.resolve("resume")).consume("version-one"))
            store.publish(state)
            assertEquals(state, store.consume("version-one"))
        } finally { dir.deleteRecursively() }
    }
    @Test fun upgradeAndCorruptMetadataFallBackWithoutReusingOldState() {
        val dir = Files.createTempDirectory("resume-test").toFile()
        try {
            val file = dir.resolve("resume")
            val store = CheckpointStore(file)
            store.publish(CheckpointStore.Saved("old", "b".repeat(43)))
            assertNull(store.consume("new"))
            assertFalse(file.exists())
            file.writeText("broken")
            assertNull(store.consume("new"))
            assertFalse(file.exists())
        } finally { dir.deleteRecursively() }
    }
    @Test fun failedInvalidationNeverReturnsACheckpoint() {
        val dir = Files.createTempDirectory("resume-test").toFile()
        try {
            val file = dir.resolve("resume")
            CheckpointStore(file).publish(CheckpointStore.Saved("one", "c".repeat(43)))
            assertThrows(java.io.IOException::class.java) {
                CheckpointStore(file) { throw java.io.IOException("fsync failed") }.consume("one")
            }
        } finally { dir.deleteRecursively() }
    }
}
