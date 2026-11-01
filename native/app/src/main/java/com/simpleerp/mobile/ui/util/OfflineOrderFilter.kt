package com.simpleerp.mobile.ui.util

import com.simpleerp.mobile.data.DocumentSummary

/**
 * 断网「本地兜底筛选」的**纯函数**（2026-11-01）。
 *
 * 背景：快照只给「今天/本周/本月/本年/全部」这些快捷档各存一份；用户按「10 月」
 * 「10/31 单日」「8–10 月」这类自定义区间查时，缓存里没有一一对应的键。断网时改成
 * 拿一份覆盖整个同步区间的「备份列表」（见服务端 `/m/api/snapshot` 的 backup_key）
 * 在本地按日期 + 类型 + 客户过滤。
 *
 * 只做**内存过滤**，不做日期换算 —— 周/月/年具体是哪几天交给服务端
 * `resolve_mobile_range`，两端口径只有一份。抽成纯函数是为了能在 JVM 单测里钉住：
 * 区间含端点、空区间返回空、排序保持输入顺序。
 */
object OfflineOrderFilter {

    /**
     * @param documents 备份列表里的单据摘要（服务端已按日期倒序）。
     * @param startDate 起（含），ISO `YYYY-MM-DD`。
     * @param endDate   止（含），ISO `YYYY-MM-DD`。
     * @param orderType 单据类型；空串 = 全部类型。
     * @param customerId 客户 id；<= 0 = 不按客户筛。
     */
    fun filter(
        documents: List<DocumentSummary>,
        startDate: String,
        endDate: String,
        orderType: String = "",
        customerId: Int = 0,
    ): List<DocumentSummary> {
        if (startDate.isEmpty() || endDate.isEmpty()) return emptyList()
        return documents.filter { doc ->
            // ISO 日期字符串可直接按字典序比较，等价于日期比较。
            doc.businessDate >= startDate && doc.businessDate <= endDate &&
                (orderType.isEmpty() || doc.orderType == orderType) &&
                (customerId <= 0 || doc.customerId == customerId)
        }
    }
}
