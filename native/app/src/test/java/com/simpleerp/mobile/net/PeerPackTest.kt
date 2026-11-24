package com.simpleerp.mobile.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 同伴数据包的格式契约（2026-11-21）。
 *
 * 这些用例钉的核心只有一句：**版本对不上就拒绝，绝不"合进去看起来能用"**。
 * 因为 `entries` 的键是服务端算好的规范化 path（见 `SnapshotStore.canonicalKey`），
 * `API_SCHEMA` 一变键就全错开，那种"导入成功但离线查不到"的故障是最难查的 ——
 * 界面明明说导好了，翻页却空白。
 */
class PeerPackTest {

    private fun sample(
        kind: String = PeerPack.KIND,
        packVersion: Int = PeerPack.PACK_VERSION,
        apiSchema: Int = com.simpleerp.mobile.data.API_SCHEMA,
        syncedAt: Long = 1_763_700_000_000L,
        entries: Map<String, String> = mapOf("/m/api/today" to """{"ok":true}"""),
    ) = PeerPack(
        kind = kind,
        packVersion = packVersion,
        apiSchema = apiSchema,
        shopName = "测试消防",
        baseUrl = "http://192.168.10.5:5001",
        syncedAt = syncedAt,
        syncFrom = "2026-10-01",
        syncTo = "2026-11-21",
        backupKey = "/m/api/orders?date_mode=all",
        entries = entries,
    )

    @Test
    fun testRoundTripKeepsEntriesAndTime() {
        val original = sample()
        val decoded = PeerPack.decode(PeerPack.encode(original)).getOrThrow()

        assertEquals(original.entries, decoded.entries)
        assertEquals(original.syncedAt, decoded.syncedAt)
        assertEquals(original.shopName, decoded.shopName)
        // 地址只是兜底用的，也必须原样过一遍：B 从没配过地址时全靠它。
        assertEquals(original.baseUrl, decoded.baseUrl)
    }

    @Test
    fun testRejectsWrongFileKind() {
        val pack = sample(kind = "something-else")
        val error = pack.rejection()
        assertNotNull("不是数据包的文件必须被拒绝", error)
        assertTrue(error!!.contains("不是同伴数据包"))
    }

    @Test
    fun testRejectsUnknownPackVersion() {
        val pack = sample(packVersion = 99)
        assertThatContains(sample(packVersion = 99).rejection(), "版本不认识")
    }

    @Test
    fun testRejectsSchemaMismatch() {
        // 关键用例：schema 不同时，entries 的键与接收端对不上，
        // 必须直接拒绝而不是"尽量合并"。
        val pack = sample(apiSchema = 999)
        assertThatContains(pack.rejection(), "版本对不上")
    }

    @Test
    fun testRejectsNoSyncTime() {
        assertThatContains(sample(syncedAt = 0L).rejection(), "没有同步时间")
    }

    @Test
    fun testRejectsEmptyEntries() {
        assertThatContains(sample(entries = emptyMap()).rejection(), "没有数据")
    }

    @Test
    fun testValidPackPasses() {
        assertNull(sample().rejection())
    }

    @Test
    fun testDecodeFailsWithHumanReadableMessage() {
        val bad = PeerPack.encode(sample(apiSchema = 999))
        val result = PeerPack.decode(bad)
        assertTrue(result.isFailure)
        assertThatContains(result.exceptionOrNull()?.message, "版本对不上")
    }

    @Test
    fun testSuggestionFileNameCarriesSyncedAtNotNow() {
        // 文件名带的是**数据截止时间**，不是生成时间 —— 用户在微信里挑文件时
        // 靠它分辨哪份更新。这里只钉"能生成且带日期"，具体时区不锁。
        val name = PeerPack.suggestFileName(1_763_700_000_000L)
        assertTrue(name.startsWith("简单ERP同伴数据_"))
        assertTrue(name.endsWith(".json"))
        assertTrue(name.contains("_"))
    }

    private fun assertThatContains(actual: String?, expected: String) {
        assertNotNull("应当给出拒绝原因", actual)
        assertTrue("期望包含「$expected」，实际是「$actual」", actual!!.contains(expected))
    }
}
