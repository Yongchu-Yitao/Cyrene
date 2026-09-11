"""Add timing ONLY to the v0.2.5 debug probe in an exported /tmp checkout."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
assert str(root).startswith('/private/tmp/') or str(root).startswith('/tmp/')
path = root / 'runtime-app/src/debug/kotlin/ai/cyrene/mobile/runtime/RuntimeProbeActivity.kt'
source = path.read_text()
source = source.replace('manager.handle(', 'measuredHandle(')
source = source.replace('val manager = QemuRuntimeManager(this)', '''val manager = QemuRuntimeManager(this)
            val probeStarted = System.nanoTime()
            fun measuredHandle(req: GuestRequest): ai.cyrene.mobile.runtime.protocol.GuestResponse {
                val began = System.nanoTime()
                val response = manager.handle(req)
                Log.i("CyreneResearchTiming", JSONObject()
                    .put("operation", req.operation.name)
                    .put("elapsed_ms", (System.nanoTime() - began) / 1e6)
                    .put("since_start_ms", (System.nanoTime() - probeStarted) / 1e6)
                    .put("status", response.status).toString())
                return response
            }''')
path.write_text(source)
