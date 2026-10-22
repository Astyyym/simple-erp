package com.simpleerp.mobile.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 二维码解析器的契约测试。
 *
 * 为什么值得单独测：这是**唯一**能把「用户不用知道 IP」这件事做成的地方。
 * 它一旦解析错，用户扫了码还是连不上，而且错误信息会指向「网络不通」，
 * 排查方向完全错。这些用例都是真实二维码可能出现的形态。
 */
class PairingQrTest {

    @Test
    fun testQrFromSettingsPage() {
        // 这是 mobile_url() 实际产出的形态
        assertEquals(
            "http://192.168.10.5:5001",
            PairingQr.parse("http://192.168.10.5:5001/m/"),
        )
    }

    @Test
    fun testQrTrailingSlash() {
        assertEquals(
            "http://192.168.10.5:5001",
            PairingQr.parse("http://192.168.10.5:5001"),
        )
    }

    @Test
    fun testQrHostPortOnly() {
        assertEquals(
            "http://192.168.10.5:5001",
            PairingQr.parse("192.168.10.5:5001"),
        )
    }

    @Test
    fun testQrHostOnly() {
        assertEquals(
            "http://192.168.10.5:5001",
            PairingQr.parse("192.168.10.5"),
        )
    }

    @Test
    fun testQrDifferentSubnets() {
        // 用户的 IP 段各不相同：192.168.0.x / 192.168.1.x / 10.0.0.x 都见过
        assertEquals("http://192.168.1.129:5001", PairingQr.parse("http://192.168.1.129:5001/m/"))
        assertEquals("http://10.0.0.8:5001", PairingQr.parse("http://10.0.0.8:5001/m/"))
        assertEquals("http://172.16.5.20:5001", PairingQr.parse("http://172.16.5.20:5001/m/"))
    }

    @Test
    fun testQrQueryStripped() {
        assertEquals(
            "http://192.168.10.5:5001",
            PairingQr.parse("http://192.168.10.5:5001/m/api/meta?x=1"),
        )
    }

    @Test
    fun testQrEmptyAndNull() {
        assertNull(PairingQr.parse(null))
        assertNull(PairingQr.parse(""))
        assertNull(PairingQr.parse("   "))
    }

    @Test
    fun testQrRejectsNonHttp() {
        // 二维码可以被任何人贴一张假的，必须挡住这些
        assertNull(PairingQr.parse("javascript:alert(1)"))
        assertNull(PairingQr.parse("file:///etc/passwd"))
        assertNull(PairingQr.parse("content://media/external/images/1"))
        assertNull(PairingQr.parse("intent://scan/#Intent;scheme=zxing;end"))
    }

    @Test
    fun testQrRejectsPlainText() {
        // 扫到别人家的二维码、或者商品条码，不该被当成地址
        assertNull(PairingQr.parse("这是一段普通文字"))
    }
}
