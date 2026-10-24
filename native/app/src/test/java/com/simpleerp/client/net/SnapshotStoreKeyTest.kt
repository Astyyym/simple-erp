package com.simpleerp.client.net

import com.simpleerp.mobile.net.SnapshotStore
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 缓存键的规范化规则必须与 Python 侧逐字一致。
 *
 * 为什么这条值得单独测：快照的 `entries` 是用 Python 的 `_canonical_path` 生成的键，
 * 客户端拿自己拼的键去查。两边规则只要差一点（键的顺序、空值算不算），
 * 表现是**断网时某个页面永远空白**，而且在线测试永远发现不了 —— 线上是走网络的。
 */
class SnapshotStoreKeyTest {

    @Test
    fun `无参数时只有路径`() {
        assertEquals("/m/api/today", SnapshotStore.canonicalKey("/m/api/today", emptyMap()))
    }

    @Test
    fun `query 按字母序排列`() {
        val key = SnapshotStore.canonicalKey(
            "/m/api/orders",
            mapOf("year" to "2026", "date_mode" to "month", "month" to "10"),
        )
        assertEquals("/m/api/orders?date_mode=month&month=10&year=2026", key)
    }

    @Test
    fun `空值参数被跳过`() {
        val key = SnapshotStore.canonicalKey(
            "/m/api/orders",
            mapOf("date_mode" to "day", "start_date" to "", "end_date" to ""),
        )
        assertEquals("/m/api/orders?date_mode=day", key)
    }

    @Test
    fun `与 Python 快照里的实际键逐字相同`() {
        // 这三个键取自真实快照响应（tests/test_mobile_snapshot.py 里也断言过）。
        assertEquals(
            "/m/api/orders?date_mode=all",
            SnapshotStore.canonicalKey("/m/api/orders", mapOf("date_mode" to "all")),
        )
        assertEquals(
            "/m/api/orders?date_mode=day&end_date=2026-10-24&start_date=2026-10-24",
            SnapshotStore.canonicalKey(
                "/m/api/orders",
                mapOf("date_mode" to "day", "start_date" to "2026-10-24", "end_date" to "2026-10-24"),
            ),
        )
        assertEquals(
            "/m/api/orders?date_mode=month&month=10&year=2026",
            SnapshotStore.canonicalKey(
                "/m/api/orders",
                mapOf("date_mode" to "month", "year" to "2026", "month" to "10"),
            ),
        )
    }

    @Test
    fun `详情路径不带 query`() {
        assertEquals(
            "/m/api/orders/sale/12",
            SnapshotStore.canonicalKey("/m/api/orders/sale/12", emptyMap()),
        )
    }
}
