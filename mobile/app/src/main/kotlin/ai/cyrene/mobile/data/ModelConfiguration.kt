package ai.cyrene.mobile.data

import org.json.JSONArray
import org.json.JSONObject

const val MODEL_CONFIGURATION_VERSION = 10
val MODEL_ROUTE_NAMES = listOf("primary", "secondary", "vision", "embedding")

fun emptyModelConfiguration(): JSONObject = JSONObject()
    .put("version", MODEL_CONFIGURATION_VERSION)
    .put("connections", JSONArray())
    .put("profiles", JSONArray())
    .put("routes", JSONObject().apply {
        MODEL_ROUTE_NAMES.forEach { put(it, JSONArray()) }
    })

/**
 * Keep only the canonical model graph plus the revision used for remote CAS.
 * Adapter/plugin catalog data belongs to the desktop settings projection and
 * must not become part of the encrypted mobile runtime configuration.
 */
fun canonicalModelConfiguration(value: JSONObject): JSONObject {
    require(value.optJSONArray("connections") != null) {
        "model configuration connections must be an array"
    }
    require(value.optJSONArray("profiles") != null) {
        "model configuration profiles must be an array"
    }
    val sourceRoutes = value.optJSONObject("routes")
        ?: throw IllegalArgumentException("model configuration routes must be an object")
    MODEL_ROUTE_NAMES.forEach { route ->
        require(sourceRoutes.optJSONArray(route) != null) {
            "model configuration route $route must be an array"
        }
    }
    val graph = emptyModelConfiguration()
    graph.put("version", value.optInt("version", MODEL_CONFIGURATION_VERSION))
    if (value.has("revision") && !value.isNull("revision")) {
        graph.put("revision", value.optLong("revision"))
    }
    graph.put(
        "connections",
        JSONArray().apply {
            value.optJSONArray("connections").modelObjects().forEach { source ->
                put(JSONObject(source.toString()).apply {
                    remove("api_key_configured")
                    remove("secret_configured")
                })
            }
        },
    )
    graph.put(
        "profiles",
        JSONArray().apply {
            value.optJSONArray("profiles").modelObjects().forEach { put(JSONObject(it.toString())) }
        },
    )
    graph.put("routes", JSONObject().apply {
        MODEL_ROUTE_NAMES.forEach { route ->
            put(route, JSONArray(sourceRoutes.optJSONArray(route).strings()))
        }
    })
    return graph
}

/** Preserve write-only connection secrets when editing a redacted graph. */
fun mergeModelConfigurationSecrets(incoming: JSONObject, previous: JSONObject?): JSONObject {
    val merged = canonicalModelConfiguration(incoming)
    val oldConnections = previous?.optJSONArray("connections").modelObjects()
        .orEmpty().associateBy { it.optString("id") }
    merged.getJSONArray("connections").modelObjects().forEach { connection ->
        val previousConnection = oldConnections[connection.optString("id")]
        val submittedSecret = connection.optString("api_key")
        when {
            connection.optBoolean("clear_api_key") -> connection.put("api_key", "")
            submittedSecret.isNotBlank() -> Unit
            else -> previousConnection?.optString("api_key")
                ?.takeIf(String::isNotBlank)
                ?.let { connection.put("api_key", it) }
        }
        connection.remove("clear_api_key")
    }
    return merged
}

fun redactModelConfigurationSecrets(value: JSONObject): JSONObject =
    canonicalModelConfiguration(value).apply {
        getJSONArray("connections").modelObjects().forEach { connection ->
            val configured = connection.optString("api_key").isNotBlank()
            connection.put("api_key", "")
            connection.put("api_key_configured", configured)
            connection.put("secret_configured", configured)
        }
    }

/** Resolve an ordered route into enabled profile + connection candidates. */
fun resolvedModelCandidates(value: JSONObject, routeName: String): List<JSONObject> {
    val connections = value.optJSONArray("connections").modelObjects()
        .orEmpty().associateBy { it.optString("id") }
    val profiles = value.optJSONArray("profiles").modelObjects()
        .orEmpty().associateBy { it.optString("id") }
    val route = value.optJSONObject("routes")?.optJSONArray(routeName).strings()
    return route.mapNotNull { profileId ->
        val profile = profiles[profileId]?.takeIf { it.optBoolean("enabled", true) }
            ?: return@mapNotNull null
        val connection = connections[profile.optString("connection_id")]
            ?.takeIf { it.optBoolean("enabled", true) }
            ?: return@mapNotNull null
        JSONObject(connection.toString()).apply {
            put("connection_id", connection.optString("id"))
            put("connection_name", connection.optString("name"))
            put("profile_id", profile.optString("id"))
            profile.keys().forEach { key -> put(key, profile.get(key)) }
            put("id", profile.optString("id"))
            put("adapter", connection.optString("adapter"))
            put("base_url", connection.optString("base_url"))
            put("api_key", connection.optString("api_key"))
        }
    }
}

fun JSONObject.modelProfile(profileId: String): JSONObject? =
    optJSONArray("profiles").modelObjects().firstOrNull { it.optString("id") == profileId }

fun JSONObject.modelConnection(connectionId: String): JSONObject? =
    optJSONArray("connections").modelObjects().firstOrNull { it.optString("id") == connectionId }

private fun JSONArray?.modelObjects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

fun JSONArray?.strings(): List<String> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { index ->
        optString(index).takeIf(String::isNotBlank)
    }
