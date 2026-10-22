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
