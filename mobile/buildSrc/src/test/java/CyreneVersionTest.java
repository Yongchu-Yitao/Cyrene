import static org.junit.Assert.*;
import org.junit.Test;

public class CyreneVersionTest {
    @Test public void currentVersionUpgradesEveryLegacyApk() {
        assertEquals(9002020, CyreneVersion.androidCode("0.9.0-beta20"));
        assertTrue(CyreneVersion.androidCode("0.9.0-beta20") > CyreneVersion.androidCode("0.9.0-beta19"));
    }

    @Test public void releasesAndPrereleasesKeepTheirOrder() {
        String[] versions = {"0.9.0-dev1", "0.9.0-dev999", "0.9.0-alpha0",
            "0.9.0-alpha999", "0.9.0-beta0", "0.9.0-beta18", "0.9.0-beta19", "0.9.0-beta20",
            "0.9.0-beta999", "0.9.0-rc0", "0.9.0-rc999", "0.9.0",
            "0.9.1-dev0", "0.9.99", "0.10.0-dev0", "0.99.99", "1.0.0-dev0"};
        for (int i = 1; i < versions.length; i++) {
            assertTrue(versions[i], CyreneVersion.androidCode(versions[i - 1]) < CyreneVersion.androidCode(versions[i]));
        }
    }

    @Test public void boundsAvoidCollisionsAndAndroidOverflow() {
        assertTrue(CyreneVersion.androidCode("20.99.99") <= 2100000000);
        for (String value : new String[]{"21.0.0", "0.100.0", "0.1.100", "0.9.0-beta1000",
            "0.9.0-preview1", "0.9.0+build1", "0.9.0-beta", "00.9.0", "0.0.0-dev0"}) {
            assertThrows(value, IllegalArgumentException.class, () -> CyreneVersion.androidCode(value));
        }
    }
}
