package ai.cyrene.mobile.runtime

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.File
import java.security.MessageDigest

class SparseDiskTest {
    @Test fun preservesDataAndTrailingHolesAcrossShortReads() {
        val bytes = ByteArray(200_003)
        bytes[0] = 12
        bytes[65_537] = 42
        bytes[131_072] = -1
        val source = object : ByteArrayInputStream(bytes) {
            override fun read(b: ByteArray, off: Int, len: Int): Int =
                super.read(b, off, minOf(len, 701))
        }
        val output = File.createTempFile("cyrene-sparse-", ".disk")
        try {
            output.writeBytes(ByteArray(300_000) { 7 })
            val digest = MessageDigest.getInstance("SHA-256")
            val counts = mutableListOf<Long>()
            SparseDisk.copy(source, output, digest) { counts.add(it) }
            assertArrayEquals(MessageDigest.getInstance("SHA-256").digest(bytes), digest.digest())
            assertEquals(bytes.size.toLong(), counts.last())
            assertEquals(true, counts.zipWithNext().all { (a, b) -> b > a })
            assertEquals(bytes.size.toLong(), output.length())
            assertArrayEquals(bytes, output.readBytes())
        } finally { output.delete() }
    }

    @Test fun preservesAllZeroDiskAndTruncatesToEmpty() {
        val output = File.createTempFile("cyrene-sparse-", ".disk")
        try {
            val bytes = ByteArray(131_073)
            SparseDisk.copy(bytes.inputStream(), output)
            assertEquals(bytes.size.toLong(), output.length())
            assertArrayEquals(bytes, output.readBytes())
            SparseDisk.copy(byteArrayOf().inputStream(), output)
            assertEquals(0L, output.length())
        } finally { output.delete() }
    }
}
