package ai.cyrene.mobile.runtime.protocol;

import ai.cyrene.mobile.runtime.protocol.IRuntimeCallback;

interface IRuntimeService {
    void submit(String requestJson, IRuntimeCallback callback);
    void cancel(String requestId);
}
