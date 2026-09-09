package ai.cyrene.mobile.protocol

import org.json.JSONArray
import org.json.JSONObject

object CanonicalJson {
    fun encode(value: Any?): String = when (value) {
        null, JSONObject.NULL -> "null"
        is JSONObject -> value.keys().asSequence().toList()
            .sortedWith(::compareCodePoints)
            .joinToString(prefix = "{", postfix = "}", separator = ",") { key ->
                "${JSONObject.quote(key)}:${encode(value.get(key))}"
            }
        is JSONArray -> (0 until value.length()).joinToString(
            prefix = "[", postfix = "]", separator = ","
        ) { encode(value.get(it)) }
        is String -> JSONObject.quote(value)
        is Boolean -> if (value) "true" else "false"
        is Number -> JSONObject.numberToString(value)
        else -> JSONObject.quote(value.toString())
    }

    private fun compareCodePoints(left: String, right: String): Int {
        val a = left.codePoints().toArray()
        val b = right.codePoints().toArray()
        for (index in 0 until minOf(a.size, b.size)) {
            if (a[index] != b[index]) return a[index].compareTo(b[index])
        }
        return a.size.compareTo(b.size)
    }

    fun without(source: JSONObject, vararg omitted: String): JSONObject {
        val result = JSONObject()
        val skip = omitted.toSet()
        source.keys().forEach { key ->
            if (key !in skip) result.put(key, source.get(key))
        }
        return result
    }
}

fun b64Url(bytes: ByteArray): String =
    java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)

fun b64UrlDecode(value: String): ByteArray =
    java.util.Base64.getUrlDecoder().decode(value)

fun b64StdDecode(value: String): ByteArray =
    java.util.Base64.getDecoder().decode(value)

fun ByteArray.hex(): String = joinToString("") { "%02x".format(it) }
