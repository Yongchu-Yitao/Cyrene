package ai.cyrene.mobile.runtime.protocol;

oneway interface IRuntimeCallback {
    void onProgress(String requestId, String progressJson);
    void onResult(String responseJson);
}
