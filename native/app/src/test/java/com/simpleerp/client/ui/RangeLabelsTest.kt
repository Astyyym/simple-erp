package com.simpleerp.mobile.ui.util

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate
import java.time.YearMonth

/**
 * 时间筛选文案的契约测试。
 *
 * 为什么值得单独测：这些字符串直接印在顶部筛选条和「已选」摘要上，
 * 一旦出现 `7–7 月` / `10/23–10/23` 这种重复端点，用户会以为筛选没生效。
 * 而这类边界只有「只点了一个月」「先点 8 月再点 7 月」很短的几种，
 * 交给模拟器肉眼看既慢又容易漏，写成用例更快也更稳。
 */
class RangeLabelsTest {

    @Test
    fun testSingleDayShowsOnce() {
        val d = LocalDate.of(2026, 10, 23)
        assertEquals("10/23", RangeLabels.days(d, d))
    }

    @Test
    fun testDayRange() {
        assertEquals(
            "10/5–10/20",
            RangeLabels.days(LocalDate.of(2026, 10, 5), LocalDate.of(2026, 10, 20)),
        )
    }

    @Test
    fun testDayRangeAcrossMonths() {
        assertEquals(
            "10/28–11/3",
            RangeLabels.days(LocalDate.of(2026, 10, 28), LocalDate.of(2026, 11, 3)),
        )
    }

    @Test
    fun testSingleMonth() {
        assertEquals("2026 年 7 月", RangeLabels.months(YearMonth.of(2026, 7), YearMonth.of(2026, 7)))
    }

    @Test
    fun testMonthRangeSameYear() {
        assertEquals("2026 年 7–8 月", RangeLabels.months(YearMonth.of(2026, 7), YearMonth.of(2026, 8)))
    }

    @Test
    fun testMonthRangeReversedInput() {
        // 先点 8 月再点 7 月是正常操作，显示不能变成「8–7 月」
        assertEquals("2026 年 7–8 月", RangeLabels.months(YearMonth.of(2026, 8), YearMonth.of(2026, 7)))
    }

    @Test
    fun testMonthRangeAcrossYears() {
        assertEquals(
            "2025/11–2026/02",
            RangeLabels.months(YearMonth.of(2025, 11), YearMonth.of(2026, 2)),
        )
    }

    @Test
    fun testSingleYear() {
        assertEquals("2026 年", RangeLabels.years(2026, 2026))
    }

    @Test
    fun testYearRange() {
        assertEquals("2025–2026 年", RangeLabels.years(2025, 2026))
        assertEquals("2025–2026 年", RangeLabels.years(2026, 2025))
    }

    @Test
    fun testButtonLabelShowsConcreteMonthAndYear() {
        // 用户 2026-10-23：不要「当月/当年」，要具体值（10 月 / 2026 年）。
        assertEquals(
            "10 月",
            RangeLabels.buttonLabel(
                com.simpleerp.mobile.data.DateRange(mode = "month", year = 2026, month = 10)
            ),
        )
        assertEquals(
            "2026 年",
            RangeLabels.buttonLabel(com.simpleerp.mobile.data.DateRange(mode = "year", year = 2026)),
        )
    }

    @Test
    fun testButtonLabelDayUsesStartDate() {
        // 按日：用起止日期里的起始日，不依赖服务端 label（离线降级时 label 可能是旧的）。
        assertEquals(
            "10/23",
            RangeLabels.buttonLabel(
                com.simpleerp.mobile.data.DateRange(
                    mode = "day",
                    startDate = "2026-10-23",
                    endDate = "2026-10-23",
                )
            ),
        )
    }

    @Test
    fun testButtonLabelFallsBackWhenServerFieldsMissing() {
        // 旧版本服务端 / 字段缺失时不能显示空白按钮，退回中文口径。
        assertEquals("本月", RangeLabels.buttonLabel(com.simpleerp.mobile.data.DateRange(mode = "month")))
        assertEquals("本年", RangeLabels.buttonLabel(com.simpleerp.mobile.data.DateRange(mode = "year")))
        assertEquals("今天", RangeLabels.buttonLabel(com.simpleerp.mobile.data.DateRange()))
        assertEquals(
            "10 月 19 日 – 10 月 25 日",
            RangeLabels.buttonLabel(
                com.simpleerp.mobile.data.DateRange(mode = "week", label = "10 月 19 日 – 10 月 25 日")
            ),
        )
        assertEquals(
            "10/5–10/20",
            RangeLabels.buttonLabel(
                com.simpleerp.mobile.data.DateRange(mode = "custom", label = "10/5–10/20")
            ),
        )
    }

    @Test
    fun testTypeLabels() {
        assertEquals("全部类型", RangeLabels.typeLabel(""))
        assertEquals("销售", RangeLabels.typeLabel("sale"))
        assertEquals("退货", RangeLabels.typeLabel("return"))
        assertEquals("拿货", RangeLabels.typeLabel("purchase"))
        assertEquals("退拿货", RangeLabels.typeLabel("purchase_return"))
    }
}
