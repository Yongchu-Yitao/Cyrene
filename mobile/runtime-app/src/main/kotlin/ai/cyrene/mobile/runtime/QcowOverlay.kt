package ai.cyrene.mobile.runtime

import java.io.File
import java.io.RandomAccessFile

/** Minimal qcow2 v2 overlay. The existing raw disk becomes its immutable backing. */
internal object QcowOverlay {
    fun create(backing: File, target: File) {
        if (target.isFile) return
        val cluster = 65536L
        val name = backing.absolutePath.toByteArray(Charsets.UTF_8)
        require(name.size < cluster - 104 && backing.length() > 0)
        val temp = File(target.parentFile, target.name + ".tmp")
        RandomAccessFile(temp, "rw").use { f ->
            f.setLength(0)
            f.setLength(cluster * 4)
            f.seek(0)
            f.writeInt(0x514649fb); f.writeInt(2)
            f.writeLong(96); f.writeInt(name.size); f.writeInt(16)
            f.writeLong(backing.length()); f.writeInt(0)
            val l1 = ((backing.length() + cluster * 8192 - 1) / (cluster * 8192)).toInt()
            require(l1 <= 8192) { "Backing disk exceeds overlay capacity" }
            f.writeInt(l1); f.writeLong(cluster * 3)
            f.writeLong(cluster); f.writeInt(1); f.writeInt(0); f.writeLong(0)
            f.writeInt(0xe2792aca.toInt()); f.writeInt(3); f.write(byteArrayOf(114, 97, 119, 0, 0, 0, 0, 0))
            f.writeLong(0); f.write(name)
            f.seek(cluster); f.writeLong(cluster * 2)
            f.seek(cluster * 2); repeat(4) { f.writeShort(1) }
            f.fd.sync()
        }
        check(temp.renameTo(target)) { "Unable to install writable disk overlay" }
    }
}
