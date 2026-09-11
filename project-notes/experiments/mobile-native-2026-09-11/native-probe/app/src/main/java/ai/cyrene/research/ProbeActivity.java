package ai.cyrene.research;

import android.app.Activity;
import android.os.Bundle;
import android.os.SystemClock;
import android.widget.TextView;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

public class ProbeActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        long start = SystemClock.elapsedRealtimeNanos();
        TextView view = new TextView(this);
        view.setText("Running isolated native Python probe...");
        setContentView(view);
        new Thread(() -> {
            JSONObject result = new JSONObject();
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                result.put("python_start_ms", (SystemClock.elapsedRealtimeNanos() - start) / 1e6);
                String report = Python.getInstance().getModule("probe").callAttr("run", getFilesDir().toString()).toString();
                result.put("probe", new JSONObject(report));
                result.put("total_ms", (SystemClock.elapsedRealtimeNanos() - start) / 1e6);
                result.put("success", true);
            } catch (Throwable error) {
                try { result.put("success", false); result.put("error", error.toString()); } catch (Exception ignored) {}
            }
            String text = result.toString();
            try (FileOutputStream out = new FileOutputStream(new File(getFilesDir(), "native-probe.json"))) {
                out.write(text.getBytes(StandardCharsets.UTF_8));
            } catch (Exception error) { android.util.Log.e("CyreneNativeProbe", "persist failed", error); }
            android.util.Log.i("CyreneNativeProbe", text);
            runOnUiThread(() -> view.setText(text));
        }).start();
    }
}
