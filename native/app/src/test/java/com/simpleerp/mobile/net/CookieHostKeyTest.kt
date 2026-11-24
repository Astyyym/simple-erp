package com.simpleerp.mobile.net

import okhttp3.Cookie
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 「按主机清 cookie」的纯逻辑测试（2026-11 审计 A6）。
 *
 * 背景：落盘改成「一条 cookie 一个键 `host:port|name`」之后，`clear()` 还是
 * 「清所有主机」的粒度。于是「改地址」（换到另一台电脑）会把**所有**电脑的
 * 配对态一起抹掉 —— 用户换回原地址时还要重新配对一次，而这次清理跟那台电脑无关。
 *
 * 这里钉两件事：只清这台主机的键（新老两种格式都覆盖），以及别把端口前缀相同的
 * 另一台电脑误伤（`:5001` 是 `:50011` 的前缀）。
 */
class CookieHostKeyTest {

    private val url = "http://192.168.10.5:5001".toHttpUrl()
    private val host = "192.168.10.5:5001"

    private fun cookie(name: String, value: String): Cookie =
        requireNotNull(
            Cookie.parse(url, "$name=$value; Path=/; Expires=Wed, 29 Dec 2027 12:00:00 GMT"),
        )

    /** 地址串 → 主机键：换地址时手上只有存下来的字符串。 */
    @Test
    fun testHostKeyFromStoredUrlString() {
        assertEquals(host, CookiePersistence.hostKeyOf("http://192.168.10.5:5001"))
        // 结尾多一个斜杠也得认（写入时 trimEnd 过，但旧数据不一定）。
        assertEquals(host, CookiePersistence.hostKeyOf("http://192.168.10.5:5001/"))
        // 认不出来返回 null：脏地址不该把清理流程弄崩。
        assertNull(CookiePersistence.hostKeyOf(""))
        assertNull(CookiePersistence.hostKeyOf("not a host"))
    }

    /** 新格式（`host:port|name`）与老格式（`host:port`）都属于同一台主机。 */
    @Test
    fun testKeysOfOneHostCoverBothFormats() {
        val mobile = cookie("erp_mobile", "a:b")
        val uid = cookie("erp_device_uid", "c56cbd90d35e4f34")
        val keys = listOf(
            CookiePersistence.entryKey(host, mobile.name),
            CookiePersistence.entryKey(host, uid.name),
            // 老版本「一台主机一条」的键
            host,
            // 另一台电脑
            CookiePersistence.entryKey("192.168.10.6:5001", "erp_mobile"),
        )
        assertEquals(
            setOf(
                CookiePersistence.entryKey(host, "erp_mobile"),
                CookiePersistence.entryKey(host, "erp_device_uid"),
                host,
            ),
            CookiePersistence.entryKeysForHost(keys, host).toSet(),
        )
    }

    /** `192.168.10.5:5001` 不是 `192.168.10.5:50011` 的前缀匹配 —— 那是另一台电脑。 */
    @Test
    fun testPortPrefixIsNotTreatedAsSameHost() {
        val other = "192.168.10.5:50011|erp_mobile"
        assertTrue(
            CookiePersistence.entryKeysForHost(listOf(other), host).isEmpty(),
        )
        // 反过来也一样。
        assertTrue(
            CookiePersistence
                .entryKeysForHost(listOf("$host|erp_mobile"), "192.168.10.5:50011")
                .isEmpty(),
        )
    }

    /** 落盘里没有这台主机时返回空列表（调用方不该因此报错或全清）。 */
    @Test
    fun testUnknownHostHasNoKeys() {
        assertTrue(
            CookiePersistence
                .entryKeysForHost(listOf("$host|erp_mobile"), "192.168.10.9:5001")
                .isEmpty(),
        )
    }
}
