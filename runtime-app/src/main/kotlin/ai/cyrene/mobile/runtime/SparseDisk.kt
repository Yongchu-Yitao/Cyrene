package ai.cyrene.mobile.runtime

import java.io.File
import java.io.InputStream
import java.io.RandomAccessFile

/** Preserve zero-filled disk regions as holes, including trailing free space. */
internal object SparseDisk {
    fun copy(input: InputStream, destination: File) {
        RandomAccessFile(destination, "rw").use { output ->
            output.setLength(0)
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                if (count == 0) continue
                var zero = true
                for (index in 0 until count) {
                    if (buffer[index] != 0.toByte()) { zero = false; break }
                }
                if (zero) output.seek(output.filePointer + count)
                else output.write(buffer, 0, count)
            }
            output.setLength(output.filePointer)
            output.fd.sync()
        }
    }
}
