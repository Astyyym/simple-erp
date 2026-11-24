package com.simpleerp.mobile.net

import okhttp3.Cookie
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 过期 cookie 的清理口径测试（2026-11 审计 A5）。
 *
 * 事故形状：`loadForRequest` 只在**内存**里 `removeAll { it.expiresAt < now }`，
 * 磁盘那份从来不删；而 `init` 冷启动又无条件把磁盘上的全装回内存。
 * 于是「过期」这件事只在当前进程内有效 —— 清一次后台、系统回收一次，
 * 服务端早已作废的 token 又活了，表现是「明明配过对，却每次请求都 401」。
 *
 * 这里钉的就是盘上要删哪些键、内存里要留哪些 cookie 两件事。
 */
class CookieExpiryTest {

    private val url = "http://192.168.10.5:5001".toHttpUrl()
    private val host = "192.168.10.5:5001"

    /** 2026-10-01 12:00 UTC 左右的历史时间点，用来当「现在」。 */
    private val now = 1_790_000_000_000L

    private fun cookie(setCookie: String): Cookie =
        requireNotNull(Cookie.parse(url, setCookie)) { "测试数据本身解析不出来：$setCookie" }

    private fun expired(name: String): Cookie =
        cookie("$name=dead; Path=/; Expires=Thu, 01 Jan 1970 00:00:10 GMT")

    private fun alive(name: String): Cookie =
        cookie("$name=ok; Path=/; Expires=Wed, 29 Dec 2027 12:00:00 GMT")

    /** 盘上过期的键必须被列出来删掉（否则下次冷启动又被装回内存）。 */
    @Test
    fun testExpiredDiskEntriesAreListed() {
        val entries = mapOf(
            CookiePersistence.entryKey(host, "erp_mobile") to expired("erp_mobile").toString(),
            CookiePersistence.entryKey(host, "erp_device_uid") to alive("erp_device_uid").toString(),
        )
        assertEquals(
            listOf(CookiePersistence.entryKey(host, "erp_mobile")),
            CookiePersistence.expiredEntryKeys(entries, now),
        )
    }

    /** 老格式（一台主机一个键）也要能判出过期：升级过渡期它就是唯一那份。 */
    @Test
    fun testLegacyEntryCanBeDetectedAsExpired() {
        val entries = mapOf(host to expired("erp_mobile").toString())
        assertEquals(listOf(host), CookiePersistence.expiredEntryKeys(entries, now))
    }

    /** 还没到期的不能动 —— 误删等于把已配对的手机打回未配对。 */
    @Test
    fun testAliveEntriesAreKept() {
        val entries = mapOf(
            CookiePersistence.entryKey(host, "erp_mobile") to alive("erp_mobile").toString(),
        )
        assertTrue(CookiePersistence.expiredEntryKeys(entries, now).isEmpty())
    }

    /** 认不出来的脏数据不在这里删：说不清它是什么，删了也没法向用户解释。 */
    @Test
    fun testBrokenEntryIsNotListedAsExpired() {
        val entries = mapOf("not a host" to "garbage-without-equals")
        assertTrue(CookiePersistence.expiredEntryKeys(entries, now).isEmpty())
    }

    /** 内存过滤：只留活着的，且不改变原列表里的顺序（同主机 cookie 是按写入顺序的）。 */
    @Test
    fun testPruneExpiredKeepsOnlyAliveCookies() {
        val mobile = alive("erp_mobile")
        val uid = expired("erp_device_uid")
        assertEquals(listOf(mobile), CookiePersistence.pruneExpired(listOf(mobile, uid), now))
        assertTrue(CookiePersistence.pruneExpired(listOf(uid), now).isEmpty())
    }
}
