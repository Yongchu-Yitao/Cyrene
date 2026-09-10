package ai.cyrene.mobile.browser

import org.junit.Assert.*
import org.junit.Test

class BrowserUrlPolicyTest {
    @Test fun permitsPublicWebPages() {
        for (url in listOf("https://example.com/path?q=hi", "http://example.com", "about:blank")) assertTrue(url, BrowserUrlPolicy.allows(url))
    }
    @Test fun blocksPrivilegedResourcesAndAmbiguousAddresses() {
        for (url in listOf("javascript:alert(1)", "file:///data/data/secret", "content://files/test", "intent://page", "data:text/html,test",
            "http://localhost:4242", "http://sub.localhost", "http://127.0.0.1:8080", "http://2130706433", "http://0x7f000001", "http://[::1]", "http://example.com@localhost/", "http://localhost./", "http://10.0.2.2", "https://example.com\\@localhost")) assertFalse(url, BrowserUrlPolicy.allows(url))
    }
}
