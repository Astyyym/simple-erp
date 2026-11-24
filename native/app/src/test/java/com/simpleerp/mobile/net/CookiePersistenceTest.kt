package com.simpleerp.mobile.net

import okhttp3.Cookie
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * cookie 落盘的回归测试（2026-11-24）。
 *
 * 事故现场：配对成功后清掉手机后台，再打开 App 又要重新输配对码。
 * 根因是**同一台主机的多条 cookie 写进了同一个键**，后写的覆盖前写的 ——
 * 内存里没事，冷启动读回来就只剩一条 `erp_device_uid`，认证用的 `erp_mobile` 没了。
 * 所以第一个用例专门钉住「配对响应下发两个 cookie，落盘后必须两个都在」。
 */
class CookiePersistenceTest {

    private val url = "http://192.168.10.5:5001".toHttpUrl()
    private val hostKey = "192.168.10.5:5001"

    private fun cookie(setCookie: String): Cookie =
        requireNotNull(Cookie.parse(url, setCookie)) { "测试数据本身解析不出来：$setCookie" }

    private fun farFuture(name: String, value: String): Cookie =
        cookie("$name=$value; Path=/; Expires=Wed, 29 Dec 2027 12:00:00 GMT; HttpOnly")

    /** 事故回归：配对响应一次下发两个 cookie，落盘后必须两个都读回来。 */
    @Test
    fun both_cookies_from_one_response_survive_reload() {
        val mobile = farFuture("erp_mobile", "abc123:def456")
        val uid = farFuture("erp_device_uid", "c56cbd90d35e4f34")

        // 模拟 saveFromResponse 的落盘：一条 cookie 一个键
        val disk = mapOf(
            CookiePersistence.entryKey(hostKey, mobile.name) to mobile.toString(),
            CookiePersistence.entryKey(hostKey, uid.name) to uid.toString(),
        )
        // 键必须互不相同 —— 同名键就是这次「只剩一条」的直接原因
        assertEquals(2, disk.size)

        val restored = CookiePersistence.decodeAll(disk).getValue(hostKey)
        assertEquals(
            setOf("erp_mobile", "erp_device_uid"),
            restored.map { it.name }.toSet(),
        )
        assertEquals("abc123:def456", restored.first { it.name == "erp_mobile" }.value)
        // 认证 cookie 的过期时间也不能在读回时丢：丢了会被 loadForRequest 当成 session，
        // 下一次冷启动直接过期。
        assertEquals(mobile.expiresAt, restored.first { it.name == "erp_mobile" }.expiresAt)
    }

    /** 老版本（一台主机一个键）的落盘仍能读回来，升级不会把已配对的人打成未配对。 */
    @Test
    fun legacy_single_entry_still_loads() {
        val legacy = mapOf(
            hostKey to "erp_mobile=old:token; Path=/; Expires=Wed, 29 Dec 2027 12:00:00 GMT",
        )
        val restored = CookiePersistence.decodeAll(legacy).getValue(hostKey)
        assertEquals(listOf("erp_mobile"), restored.map { it.name })
        assertEquals("old:token", restored.first().value)
    }

    /** 新老键同时存在时新格式胜出，不会读出两条同名 cookie。 */
    @Test
    fun new_entry_wins_over_legacy_for_same_name() {
        val disk = mapOf(
            hostKey to "erp_mobile=stale:token; Path=/; Expires=Wed, 29 Dec 2027 12:00:00 GMT",
            CookiePersistence.entryKey(hostKey, "erp_mobile") to
                "erp_mobile=fresh:token; Path=/; Expires=Wed, 29 Dec 2027 12:00:00 GMT",
        )
        val restored = CookiePersistence.decodeAll(disk).getValue(hostKey)
        assertEquals(1, restored.size)
        assertEquals("fresh:token", restored.first().value)
    }

    /** 坏数据（主机认不出 / cookie 内容坏）跳过，不能让整个配对态读崩。 */
    @Test
    fun broken_entries_are_skipped() {
        val disk = mapOf(
            "not a host" to "erp_mobile=x",
            CookiePersistence.entryKey(hostKey, "erp_mobile") to "garbage-without-equals",
        )
        assertTrue(CookiePersistence.decodeAll(disk).isEmpty())
    }

    /** 换端口/换电脑 = 换主机键：旧主机的 cookie 不会被送到新主机。 */
    @Test
    fun cookies_are_keyed_by_host_and_port() {
        val other = farFuture("erp_mobile", "x:y")
        val disk = mapOf(
            CookiePersistence.entryKey("192.168.10.5:5001", "erp_mobile") to other.toString(),
        )
        val loaded = CookiePersistence.decodeAll(disk)
        assertTrue(loaded.containsKey("192.168.10.5:5001"))
        assertTrue(!loaded.containsKey("192.168.10.5:5002"))
    }
}
