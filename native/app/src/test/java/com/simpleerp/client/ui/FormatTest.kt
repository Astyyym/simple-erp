package com.simpleerp.mobile.ui.util

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate

/**
 * 本地日期文案的契约测试。
 *
 * 为什么要单独钉：手机端「今天」的日期现在由**手机自己**算（2026-10-23 修
 * 「电脑一断连，首页日期就不刷新」），而服务端 `mobile_format.py` 里也有一套
 * `month_day_label` / `weekday_label`。两套实现必须逐字符一致 ——
 * 否则同一天在「今日概览」和「单据列表」上会显示成 `10月23日` 与 `10月23号`，
 * 用户会以为看的是两个系统。
 *
 * 同时钉住边界：个位数月份不能写成 `01月`（服务端也是 `1月`），
 * 跨月/跨年/闰日不能算错。
 */
class FormatTest {

    @Test
    fun testMonthDayDropsLeadingZeroLikeServer() {
        // 服务端 mobile_format.month_day_label 用的是 int()，所以是「1月5日」不是「01月05日」。
        assertEquals("1月5日", monthDayLabel("2026-01-05"))
        assertEquals("10月23日", monthDayLabel("2026-10-23"))
        assertEquals("12月31日", monthDayLabel("2026-12-31"))
    }

    @Test
    fun testMonthDayKeepsShortInputVerbatim() {
        // 服务端遇到长度不足 10 的输入原样返回，本地必须一致（不抛异常、不瞎猜）。
        assertEquals("", monthDayLabel(""))
        assertEquals("2026-1", monthDayLabel("2026-1"))
    }

    @Test
    fun testWeekdayMondayIsMonday() {
        // 2026-10-19 是周一。服务端 names 数组也是周一开头，索引 = weekday()（周一=0）。
        assertEquals("周一", weekdayLabel("2026-10-19"))
        assertEquals("周二", weekdayLabel("2026-10-20"))
        assertEquals("周五", weekdayLabel("2026-10-23"))
        assertEquals("周六", weekdayLabel("2026-10-24"))
        assertEquals("周日", weekdayLabel("2026-10-25"))
    }

    @Test
    fun testWeekdayAcrossYearBoundary() {
        // 2027-01-01 是周五。跨年时不能算成上一年的第 53 周。
        assertEquals("周五", weekdayLabel("2027-01-01"))
        assertEquals("周四", weekdayLabel("2026-12-31"))
    }

    @Test
    fun testWeekdayOnLeapDay() {
        // 2028 是闰年，2-29 存在且是周二。
        assertEquals("周二", weekdayLabel("2028-02-29"))
    }

    @Test
    fun testWeekdayOnBadInputIsEmptyNotCrash() {
        // 断网时服务端可能给空串/脏值；这里必须安静返回空，不能让首页崩。
        assertEquals("", weekdayLabel(""))
        assertEquals("", weekdayLabel("not-a-date"))
        assertEquals("", weekdayLabel("2026-13-45"))
    }

    @Test
    fun testLocalTodayIsoIsParseableAndIsToday() {
        val iso = localTodayIso()
        // 必须是 LocalDate 能解析的 ISO 串，且等于设备本地今天。
        assertEquals(LocalDate.now(), LocalDate.parse(iso))
        // 长度固定 10（`2026-10-23`），前端拼接不会因为补零与否分叉。
        assertEquals(10, iso.length)
    }

    @Test
    fun testMonthDayAndWeekdayAgreeOnSameDay() {
        // 同一个日期两边都要能出结果 —— 首页那两行是并排渲染的。
        val iso = "2026-10-23"
        assertEquals("10月23日", monthDayLabel(iso))
        assertEquals("周五", weekdayLabel(iso))
    }
}
