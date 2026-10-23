package com.simpleerp.mobile.ui.util

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * 缓存时间戳 → `10月21日 14:21`。
 *
 * 只用于**离线提示**：告诉用户「你看到的是几点钟的数据」。
 * 实时数据不显示这个时间 —— 那个由服务端的 `date_label` / `time` 给，
 * 客户端不重复实现一套日期口径。
 *
 * 时区必须显式取 `TimeZone.getDefault()`：模拟器默认时区是 UTC，
 * 不指定的话 14:21 会被显示成 06:21 —— 店主会以为数据是早上六点的。
 * 服务端时间本来就是按**电脑本地时间**给的，这里必须对齐到**手机本地时间**。
 */
fun formatCacheTime(epochMillis: Long): String {
    if (epochMillis <= 0L) return "上次同步"
    val formatter = SimpleDateFormat("M月d日 HH:mm", Locale.CHINA)
    formatter.timeZone = TimeZone.getDefault()
    return formatter.format(Date(epochMillis))
}

/*
 * 「今天是几号」必须由**手机自己**回答，不能等桌面端。
 *
 * 2026-10-23 用户报的真缺陷：电脑一关，首页「今天」就一直停在**上次联网那天**的日期，
 * 连日期都不刷新。原因是 TodayHeader 直接用服务端的 `date_label` / `weekday`，
 * 断网走缓存时拿到的是旧响应里的字符串 —— 一个「今天」却显示昨天，而且永远不动。
 *
 * 日期是设备本地事实，跟业务数据不是一回事：
 * - 业务数据（单据、金额）只能来自服务端，离线时用缓存并明确标注「这是旧数据」；
 * - 「今天几号」手机自己就知道，断网、跨零点都不该受影响。
 *
 * 所以这里单独给一套本地日期文案，格式与服务端 `mobile_format.py` 完全一致
 * （`10月23日` / `周五`），两个入口并排看不会出现两种写法。
 */

private val WEEKDAY_NAMES = arrayOf("周一", "周二", "周三", "周四", "周五", "周六", "周日")

/** `2026-10-23` → `10月23日`。与服务端 `month_day_label` 同格式。 */
fun monthDayLabel(isoDate: String): String {
    val text = isoDate.trim()
    if (text.length < 10) return text
    val month = text.substring(5, 7).toIntOrNull() ?: return text
    val day = text.substring(8, 10).toIntOrNull() ?: return text
    return "${month}月${day}日"
}

/** `2026-10-23` → `周五`。与服务端 `weekday_label` 同格式（周一 = 1）。 */
fun weekdayLabel(isoDate: String): String {
    val day = runCatching { java.time.LocalDate.parse(isoDate.trim()) }.getOrNull() ?: return ""
    return WEEKDAY_NAMES[day.dayOfWeek.value - 1]
}

/** 手机本地「今天」的 ISO 日期（`2026-10-23`）。 */
fun localTodayIso(): String = java.time.LocalDate.now().toString()
