import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Fixed, ordered encoding of the shared release version for Android. */
public final class CyreneVersion {
    private static final Pattern VERSION = Pattern.compile(
        "(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(?:-(dev|alpha|beta|rc)(0|[1-9][0-9]*))?"
    );

    private CyreneVersion() {}

    public static int androidCode(String version) {
        Matcher match = VERSION.matcher(version);
        if (!match.matches()) throw new IllegalArgumentException("Unsupported Cyrene release version: " + version);
        int major = Integer.parseInt(match.group(1));
        int minor = Integer.parseInt(match.group(2));
        int patch = Integer.parseInt(match.group(3));
        int sequence = match.group(5) == null ? 0 : Integer.parseInt(match.group(5));
        if (major > 20 || minor > 99 || patch > 99 || sequence > 999) {
            throw new IllegalArgumentException("Cyrene version exceeds Android encoding bounds: " + version);
        }
        int phase = match.group(4) == null ? 9 : switch (match.group(4)) {
            case "dev" -> 0;
            case "alpha" -> 1;
            case "beta" -> 2;
            case "rc" -> 3;
            default -> throw new IllegalArgumentException("Unsupported release phase");
        };
        int code = ((major * 100 + minor) * 100 + patch) * 10000 + phase * 1000 + sequence;
        if (code == 0) throw new IllegalArgumentException("Android version code must be positive");
        return code;
    }
}
