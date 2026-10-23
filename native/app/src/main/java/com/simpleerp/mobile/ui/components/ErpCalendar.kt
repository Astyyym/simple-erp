package com.simpleerp.mobile.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.ui.theme.LocalErpColors
import java.time.LocalDate
import java.time.YearMonth

/**
 * 自绘中文月历（**不用 material3 的 DateRangePicker**）。
 *
 * 为什么替换（用户 2026-10-23）：「日历是 google 原生的日历」——material3 的
 * `DateRangePicker` 是 Google 那套整页日历，月份标题、星期缩写、选择器头部
 * 都跟这个 App 的视觉语言不一致，而且它自带一整页高度，塞进「更多筛选」里
 * 会把下面的单据列表整个挤走。这里按 App 自己的 token 画一张紧凑月历：
 *
 * - **周一起始**（与「当周 = ISO 周」口径一致），表头 `一二三四五六日`；
 * - 起止点实心蓝圆、区间内浅蓝底，一眼看出选的是哪一段；
 * - 未来日期灰掉不可点（查单据没有「明天」）；
 * - 今天用蓝字标记，不额外加图形，避免和选中态混淆。
 *
 * 只负责「画 + 点」，选择逻辑在调用方（按月 / 按年各有不同的落点规则）。
 */
@Composable
fun MonthCalendar(
    month: YearMonth,
    start: LocalDate?,
    end: LocalDate?,
    today: LocalDate,
    onPick: (LocalDate) -> Unit,
    onMonthChange: (YearMonth) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current

    Column(modifier = modifier.fillMaxWidth()) {
        // ---- 月份切换 ----
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            MonthArrow("‹") { onMonthChange(month.minusMonths(1)) }
            Text(
                text = "${month.year} 年 ${month.monthValue} 月",
                color = colors.ink,
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                textAlign = TextAlign.Center,
                modifier = Modifier.weight(1f),
            )
            MonthArrow("›") { onMonthChange(month.plusMonths(1)) }
        }

        Spacer(Modifier.height(6.dp))

        // ---- 星期表头（周一起始）----
        Row(Modifier.fillMaxWidth()) {
            listOf("一", "二", "三", "四", "五", "六", "日").forEach { label ->
                Text(
                    text = label,
                    color = colors.muted,
                    fontSize = 11.sp,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.weight(1f),
                )
            }
        }

        Spacer(Modifier.height(4.dp))

        // ---- 日期网格 ----
        val lead = month.atDay(1).dayOfWeek.value - 1 // 周一 = 0
        val days = month.lengthOfMonth()
        val rows = (lead + days + 6) / 7
        for (row in 0 until rows) {
            Row(Modifier.fillMaxWidth()) {
                for (col in 0..6) {
                    val dayNumber = row * 7 + col - lead + 1
                    if (dayNumber in 1..days) {
                        DayCell(
                            date = month.atDay(dayNumber),
                            start = start,
                            end = end,
                            today = today,
                            onPick = onPick,
                            modifier = Modifier.weight(1f),
                        )
                    } else {
                        Spacer(Modifier.weight(1f).height(40.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun MonthArrow(label: String, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    Box(
        modifier = Modifier
            .size(34.dp)
            .clip(CircleShape)
            .background(colors.soft)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = colors.ink, fontSize = 16.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun DayCell(
    date: LocalDate,
    start: LocalDate?,
    end: LocalDate?,
    today: LocalDate,
    onPick: (LocalDate) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    val isStart = start != null && date == start
    val isEnd = end != null && date == end
    val inRange = start != null && end != null && date.isAfter(start) && date.isBefore(end)
    val enabled = !date.isAfter(today)
    val isToday = date == today
    val filled = isStart || isEnd

    Box(
        modifier = modifier.height(40.dp),
        contentAlignment = Alignment.Center,
    ) {
        // 区间底色：整格铺满，端点圆压在它上面，视觉上连成一条。
        if (inRange) {
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(34.dp)
                    .background(colors.primarySoft),
            )
        }
        Box(
            modifier = Modifier
                .size(32.dp)
                .clip(CircleShape)
                .background(if (filled) colors.primary else Color.Transparent)
                .clickable(enabled = enabled) { onPick(date) },
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = date.dayOfMonth.toString(),
                color = when {
                    filled -> colors.primaryInk
                    !enabled -> colors.muted.copy(alpha = 0.38f)
                    isToday -> colors.primary
                    else -> colors.ink
                },
                fontSize = 13.sp,
                fontWeight = if (filled || isToday) FontWeight.SemiBold else FontWeight.Normal,
            )
        }
    }
}

/**
 * 区间选择用的整格按钮：按月 / 按年选择共用。
 *
 * 与月历同构（选中实心、区间浅底、越界灰字），但标签是「10 月」「2024 年」。
 * 之所以不直接复用 `FilterChip`：那是「一排可横滚的胶囊」，这里是**固定 4 列网格**，
 * 12 个月 / N 个年份一屏平铺，不用滚也不会被裁。
 */
@Composable
fun RangeGridCell(
    label: String,
    selected: Boolean,
    inRange: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    val shape = RoundedCornerShape(9.dp)
    Box(
        modifier = modifier
            .height(42.dp)
            .padding(horizontal = 3.dp, vertical = 3.dp)
            .clip(shape)
            .background(
                when {
                    selected -> colors.primary
                    inRange -> colors.primarySoft
                    else -> colors.soft
                }
            )
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = label,
            color = when {
                selected -> colors.primaryInk
                !enabled -> colors.muted.copy(alpha = 0.38f)
                else -> colors.ink
            },
            fontSize = 13.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            maxLines = 1,
        )
    }
}
