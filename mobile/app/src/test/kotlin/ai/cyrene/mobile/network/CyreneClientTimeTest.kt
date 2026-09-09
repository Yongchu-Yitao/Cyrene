package ai.cyrene.mobile.network

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Test

class CyreneClientTimeTest {
    @Test
    fun parsesPythonUtcIsoTimestampWithMicroseconds() {
        assertEquals(
            Instant.parse("2026-08-02T09:02:20.184601Z"),
            parseProtocolInstant("2026-08-02T09:02:20.184601+00:00"),
        )
    }

    @Test
    fun parsesCanonicalUtcInstant() {
        assertEquals(
            Instant.parse("2026-08-02T09:02:20.184601Z"),
            parseProtocolInstant("2026-08-02T09:02:20.184601Z"),
        )
    }

    @Test
    fun convertsNonUtcOffsetToTheSameInstant() {
        assertEquals(
            Instant.parse("2026-08-02T09:02:20.184601Z"),
            parseProtocolInstant("2026-08-02T17:02:20.184601+08:00"),
        )
    }

    @Test
    fun endpointRecoveryTriesSavedPortThenTheDocumentedFallbackRange() {
        val candidates = directControlPortCandidates(37848)

        assertEquals(37848, candidates.first())
        assertEquals(100, candidates.size)
        assertEquals(37940, candidates[1])
        assertEquals(37841, candidates.last())
        assertEquals(1, candidates.count { it == 37848 })
    }
}
