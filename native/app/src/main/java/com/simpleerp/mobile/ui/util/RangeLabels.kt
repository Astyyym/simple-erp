package com.simpleerp.mobile.ui.util

import com.simpleerp.mobile.data.DateRange
import java.time.LocalDate
import java.time.YearMonth

/**
 * 时间筛选的**文案拼装**（纯函数，可在 JVM 单测里验）。
 *
 * 抽出来的原因：这些字符串会直接印在顶部筛选条和「更多筛选」的「已选」行上，
 * 一旦出现 `10–10 月`、`7/23–7/23` 这种重复端点，界面立刻显得没做完。
 * 放在 UI 文件里就只能靠肉眼看模拟器，放这里可以钉死。
 *
 * 真正的**日期换算**仍然在服务端（`date_mode` + start/end），这里只做显示。
 */
object RangeLabels {

    /** `10/23`：起止摘要用，比 ISO 日期好读。 */
    fun md(date: LocalDate): String = "${date.monthValue}/${date.dayOfMonth}"

    /** 按日区间：单日只写一次（`10/23`），跨日写 `10/23–10/30`。 */
    fun days(start: LocalDate, end: LocalDate): String =
        if (start == end) md(start) else "${md(start)}–${md(end)}"

    /**
     * 按月区间：`2026 年 7 月` / `2026 年 7–8 月` / `2025/11–2026/02`。
     *
     * 参数允许「反向传入」（先点 8 月再点 7 月），内部先排序，避免显示 `8–7 月`。
     */
    fun months(first: YearMonth, second: YearMonth): String {
        val lo = minOf(first, second)
        val hi = maxOf(first, second)
        return when {
            lo == hi -> "${lo.year} 年 ${lo.monthValue} 月"
            lo.year == hi.year -> "${lo.year} 年 ${lo.monthValue}–${hi.monthValue} 月"
            else -> "${lo.year}/${pad2(lo.monthValue)}–${hi.year}/${pad2(hi.monthValue)}"
        }
    }

    /** 按年区间：`2026 年` / `2025–2026 年`。 */
    fun years(first: Int, second: Int): String {
        val lo = minOf(first, second)
        val hi = maxOf(first, second)
        return if (lo == hi) "$lo 年" else "$lo–$hi 年"
    }

    /**
     * 顶部时间按钮上显示的文案。
     *
     * 用户 2026-10-23 要求：「选择当月、当年的话，不要显示当月、当年，显示 10 月、2026 年」。
     * 理由很直接 —— 「当月/当年」是相对词，看到的人不知道到底指哪个月；
     * 而这个按钮本身就在回答「现在筛的是哪一段」，写具体值才答得上。
     * 于是：
     * - 按月 → `10 月`（优先用 `range.month`，服务端回包也会填）；
     * - 按年 → `2026 年`；
     * - 按日 → `10/23`；
     * - 本周 → 服务端给的区间文案（`10 月 19 日 – 10 月 25 日`），客户端不自己算周；
     * - 自定义 → 用户在面板里选出来的区间。
     */
    fun buttonLabel(range: DateRange): String = when (range.mode) {
        "month" -> if (range.month > 0) "${range.month} 月" else range.label.ifEmpty { "本月" }
        "year" -> if (range.year > 0) "${range.year} 年" else range.label.ifEmpty { "本年" }
        "week" -> range.label.ifEmpty { "本周" }
        "custom" -> range.label
        else -> {
            val day = runCatching { LocalDate.parse(range.startDate) }.getOrNull()
            when {
                day != null -> md(day)
                range.label.isNotEmpty() -> range.label
                else -> "今天"
            }
        }
    }

    /** 单据类型的中文名（空 = 全部）。 */
    fun typeLabel(orderType: String): String = when (orderType) {
        "sale" -> "销售"
        "return" -> "退货"
        "purchase" -> "拿货"
        "purchase_return" -> "退拿货"
        else -> "全部类型"
    }

    private fun pad2(value: Int): String = value.toString().padStart(2, '0')
}
