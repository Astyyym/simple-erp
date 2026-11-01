package com.simpleerp.mobile.ui.util

import com.simpleerp.mobile.data.DocumentSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 断网本地兜底筛选的回归测试（2026-11-01）。
 *
 * 钉住三件事，任何一条错了都会让「断网翻 10 月」再次坏掉：
 * 1. 区间**含两端**（10/1 与 10/31 都要在结果里）；
 * 2. 类型 / 客户过滤正确；
 * 3. 保持服务端给的顺序（列表已按日期倒序，本地筛不能打乱）。
 */
class OfflineOrderFilterTest {

    private fun doc(
        date: String,
        type: String = "sale",
        customer: Int = 1,
    ) = DocumentSummary(
        id = date.hashCode(),
        orderType = type,
        businessDate = date,
        customerId = customer,
    )

    @Test
    fun `keeps documents inside the range inclusive`() {
        val docs = listOf(
            doc("2026-09-30"),
            doc("2026-10-01"),
            doc("2026-10-15"),
            doc("2026-10-31"),
            doc("2026-11-01"),
        )
        val out = OfflineOrderFilter.filter(docs, "2026-10-01", "2026-10-31")
        assertEquals(
            listOf("2026-10-01", "2026-10-15", "2026-10-31"),
            out.map { it.businessDate },
        )
    }

    @Test
    fun `single day range keeps only that day`() {
        val docs = listOf(doc("2026-10-30"), doc("2026-10-31"), doc("2026-11-01"))
        val out = OfflineOrderFilter.filter(docs, "2026-10-31", "2026-10-31")
        assertEquals(listOf("2026-10-31"), out.map { it.businessDate })
    }

    @Test
    fun `filters by order type`() {
        val docs = listOf(
            doc("2026-10-10", type = "sale"),
            doc("2026-10-11", type = "return"),
            doc("2026-10-12", type = "purchase"),
        )
        val out = OfflineOrderFilter.filter(docs, "2026-10-01", "2026-10-31", orderType = "return")
        assertEquals(listOf("2026-10-11"), out.map { it.businessDate })
    }

    @Test
    fun `filters by customer`() {
        val docs = listOf(
            doc("2026-10-10", customer = 1),
            doc("2026-10-11", customer = 2),
        )
        val out = OfflineOrderFilter.filter(docs, "2026-10-01", "2026-10-31", customerId = 2)
        assertEquals(listOf("2026-10-11"), out.map { it.businessDate })
    }

    @Test
    fun `empty range returns empty`() {
        val docs = listOf(doc("2026-10-10"))
        assertTrue(OfflineOrderFilter.filter(docs, "", "2026-10-31").isEmpty())
        assertTrue(OfflineOrderFilter.filter(docs, "2026-10-01", "").isEmpty())
    }

    @Test
    fun `preserves input order`() {
        val docs = listOf(
            doc("2026-10-31"),
            doc("2026-10-15"),
            doc("2026-10-01"),
        )
        val out = OfflineOrderFilter.filter(docs, "2026-10-01", "2026-10-31")
        assertEquals(listOf("2026-10-31", "2026-10-15", "2026-10-01"), out.map { it.businessDate })
    }
}
