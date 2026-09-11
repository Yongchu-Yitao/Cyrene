package ai.cyrene.mobile.runtime

import org.junit.Assert.*
import org.junit.Test
import java.io.RandomAccessFile
import java.nio.file.Files

class QcowOverlayTest {
    @Test fun overlayKeepsExistingRawDataAndHasExplicitBackingFormat() {
        val dir = Files.createTempDirectory("qcow-test").toFile()
        try {
            val raw = dir.resolve("root.ext4").apply { writeText("existing user data") }
            val qcow = dir.resolve("root.qcow2")
            QcowOverlay.create(raw, qcow)
            RandomAccessFile(qcow, "r").use {
                assertEquals(0x514649fb, it.readInt()); assertEquals(2, it.readInt())
                assertEquals(96L, it.readLong()); val nameSize = it.readInt()
                assertEquals(16, it.readInt()); assertEquals(raw.length(), it.readLong())
                it.seek(72); assertEquals(0xe2792aca.toInt(), it.readInt())
                assertEquals(3, it.readInt()); assertEquals("raw", String(ByteArray(3).also(it::readFully)))
                it.seek(96); assertEquals(raw.absolutePath, String(ByteArray(nameSize).also(it::readFully)))
                it.seek(65536); assertEquals(131072L, it.readLong())
                it.seek(131072); repeat(4) { _ -> assertEquals(1, it.readUnsignedShort()) }
            }
            val bytes = qcow.readBytes()
            QcowOverlay.create(raw, qcow)
            assertArrayEquals(bytes, qcow.readBytes())
            assertEquals("existing user data", raw.readText())
        } finally { dir.deleteRecursively() }
    }
}
