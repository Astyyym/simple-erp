package com.simpleerp.mobile.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.DateRangePicker
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.SelectableDates
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDateRangePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.data.DateRange
import com.simpleerp.mobile.data.OrdersResponse
import com.simpleerp.mobile.ui.UiState
import com.simpleerp.mobile.ui.components.EmptyState
import com.simpleerp.mobile.ui.components.ErpCard
import com.simpleerp.mobile.ui.components.ErpHairline
import com.simpleerp.mobile.ui.components.ErrorState
import com.simpleerp.mobile.ui.components.LoadingState
import com.simpleerp.mobile.ui.components.OfflineBanner
import com.simpleerp.mobile.ui.components.Refreshable
import com.simpleerp.mobile.ui.components.SectionLabel
import com.simpleerp.mobile.ui.components.listRowBorder
import com.simpleerp.mobile.ui.components.listRowShape
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.util.formatCacheTime
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset

/**
 * 单据列表：类型筛选 + 日期筛选，两者可叠加、互不覆盖。
 *
 * 日期换算规则**不在客户端算**：`date_mode` 与 `start_date`/`end_date` 直接交给服务端，
 * 由它复用桌面端 `_resolve_date_range()` 的换算。客户端只负责把用户点的东西
 * 变成这几个参数，避免出现「手机端按 31 天算、桌面端按月末算」这种分叉。
 *
 * 2026-10-23 改版（用户要求）：
 * - 日期条改成一行快捷项：**今天 / 当周 / 当月 / 当年 / 更多筛选**；
 * - 选中项有蓝色高亮（原来是白底纯文字，看不出当前选的是哪个）；
 * - 「更多筛选」选完自定义区间后，chip 上显示用户选的**日期区间 / 月份 / 年份**；
 * - 周/月/年都按日历定义（周 = ISO 周一→周日，由服务端算）。
 */
@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
fun OrdersScreen(
    state: UiState<OrdersResponse>,
    orderType: String,
    dateRange: DateRange,
    customerName: String,
    /** 列表滚动状态由上层持有（切 tab / 返回详情不丢位置，2026-10-22 修）。 */
    listState: LazyListState,
    refreshing: Boolean,
    onRefresh: () -> Unit,
    onSelectType: (String) -> Unit,
    onSelectRange: (DateRange) -> Unit,
    onClearCustomer: () -> Unit,
    onOpenDocument: (orderType: String, id: Int) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    var showMore by remember { mutableStateOf(false) }

    Column(modifier = modifier.fillMaxWidth()) {
        // ---- 客户筛选条（从客户详情跳进来时才有）----
        if (customerName.isNotEmpty()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = Dimens.pageGap, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("客户：$customerName", color = colors.ink, fontSize = 13.sp)
                Spacer(Modifier.width(10.dp))
                Text(
                    "清除",
                    color = colors.primary,
                    fontSize = 12.5.sp,
                    modifier = Modifier.clickable(onClick = onClearCustomer).padding(4.dp),
                )
            }
        }

        // ---- 日期快捷条（时间在上、类型在下；用户 2026-10-21 定的顺序）----
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = Dimens.pageGap, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val today = LocalDate.now()
            DateQuickChip("今天", dateRange.mode == "day") {
                val iso = today.toString()
                onSelectRange(
                    DateRange(
                        mode = "day",
                        startDate = iso,
                        endDate = iso,
                        year = today.year,
                        month = today.monthValue,
                        label = "${today.monthValue} 月 ${today.dayOfMonth} 日",
                    )
                )
            }
            DateQuickChip("当周", dateRange.mode == "week") {
                // 起止日期由服务端按 ISO 周算，这里只传 mode。
                onSelectRange(DateRange(mode = "week", label = "当周"))
            }
            DateQuickChip("当月", dateRange.mode == "month") {
                onSelectRange(
                    DateRange(
                        mode = "month",
                        year = today.year,
                        month = today.monthValue,
                        label = "${today.year} 年 ${today.monthValue} 月",
                    )
                )
            }
            DateQuickChip("当年", dateRange.mode == "year") {
                onSelectRange(DateRange(mode = "year", year = today.year, label = "${today.year} 年"))
            }
            // 「更多筛选」：自定义区间时才亮蓝；选完直接把用户选的区间写在 chip 上。
            val custom = dateRange.mode == "custom"
            DateQuickChip(
                if (custom && dateRange.label.isNotEmpty()) dateRange.label else "更多筛选",
                custom,
            ) { showMore = true }

            Spacer(Modifier.width(4.dp))
            if (state is UiState.Ready) {
                Text("${state.value.documents.size} 张", color = colors.muted, fontSize = 12.sp)
            }
        }

        if (showMore) {
            MoreFilterDialog(
                current = dateRange,
                onPick = {
                    showMore = false
                    onSelectRange(it)
                },
                onDismiss = { showMore = false },
            )
        }

        // ---- 类型筛选 ----
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = Dimens.pageGap, vertical = 2.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            FilterChip(
                label = "全部类型",
                selected = orderType.isEmpty(),
                onClick = { onSelectType("") },
            )
            FilterChip("销售", orderType == "sale") { onSelectType("sale") }
            FilterChip("退货", orderType == "return") { onSelectType("return") }
            FilterChip("拿货", orderType == "purchase") { onSelectType("purchase") }
            FilterChip("退拿货", orderType == "purchase_return") { onSelectType("purchase_return") }
        }

        when (state) {
            is UiState.Loading -> LoadingState()
            is UiState.Failed -> ErrorState(state.message, onAction = onRetry)
            UiState.NeedsPairing, UiState.NeedsSetup -> LoadingState()
            is UiState.Ready -> {
                val documents = state.value.documents
                Refreshable(refreshing = refreshing, onRefresh = onRefresh) {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(
                            start = Dimens.pageGap,
                            end = Dimens.pageGap,
                            top = 8.dp,
                            bottom = 28.dp,
                        ),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        if (state.fromCache) {
                            item { OfflineBanner(formatCacheTime(state.fetchedAt)) }
                        }
                        if (documents.isEmpty()) {
                            item {
                                EmptyState(
                                    title = "这段时间没有单据",
                                    hint = "点上面的日期按钮，可以改成「更多筛选」看更早的单。",
                                )
                            }
                        }
                        // **每行一个 item**（2026-10-22 修 N-2）：一整个 Column 塞单个 item
                        // 时，切 tab 重新测量会把滚动偏移夹到 0（实测 offset 存着却回顶部）。
                        // 视觉仍是一张整卡：ErpCard 首行圆上角、末行圆下角、中间直角。
                        documents.forEachIndexed { index, doc ->
                            item(key = "${doc.orderType}-${doc.id}") {
                                ErpCard(
                                    padding = 0.dp,
                                    elevated = index == 0,
                                    bordered = false,
                                    shape = listRowShape(
                                        isFirst = index == 0,
                                        isLast = index == documents.lastIndex,
                                    ),
                                    modifier = Modifier.listRowBorder(
                                        colors = colors,
                                        isFirst = index == 0,
                                        isLast = index == documents.lastIndex,
                                    ),
                                ) {
                                    DocumentRow(
                                        doc = doc,
                                        onClick = { onOpenDocument(doc.orderType, doc.id) },
                                    )
                                    if (index != documents.lastIndex) {
                                        ErpHairline(Modifier.padding(horizontal = 16.dp))
                                    }
                                }
                            }
                        }
                        if (state.value.truncated) {
                            item {
                                Text(
                                    "只显示了前 200 张，缩小日期范围可以看到更早的。",
                                    color = colors.muted,
                                    fontSize = 11.5.sp,
                                    modifier = Modifier.padding(vertical = 10.dp),
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

/**
 * 日期快捷 chip。
 *
 * 选中态是**实心蓝底 + 白字**（与类型筛选 chip 一致）：原来日期是白底纯文字，
 * 五个选项里看不出当前在看哪一段（用户 2026-10-23 要求「icon 要有选中的蓝色标识」）。
 * 未选中用 soft 灰底，谁都能看出这是按钮。
 */
@Composable
private fun DateQuickChip(label: String, selected: Boolean, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    Box(
        modifier = Modifier
            .height(36.dp)
            .clip(RoundedCornerShape(18.dp))
            .background(if (selected) colors.primary else colors.soft)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            label,
            color = if (selected) colors.primaryInk else colors.ink,
            fontSize = 12.5.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            maxLines = 1,
        )
    }
}

/**
 * 「更多筛选」：三种自定义口径 —— 日到日 / 月到月 / 年到年。
 *
 * 用户原话：「更多筛选（日到日，月到月，年）」。
 * 日到日用 material3 的 `DateRangePicker`（原生日历，点两下选起止）；
 * 月到月 / 年到年用现成的 chip 组（月份 12 个、年份近三年）。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MoreFilterDialog(
    current: DateRange,
    onPick: (DateRange) -> Unit,
    onDismiss: () -> Unit,
) {
    val colors = LocalErpColors.current
    val today = LocalDate.now()
    var mode by remember { mutableStateOf("day") }
    var yearFrom by remember { mutableStateOf(today.year) }
    var yearTo by remember { mutableStateOf(today.year) }
    var monthFrom by remember { mutableStateOf(today.monthValue) }
    var monthTo by remember { mutableStateOf(today.monthValue) }
    var showDayPicker by remember { mutableStateOf(false) }
    var dayStart by remember { mutableStateOf("") }
    var dayEnd by remember { mutableStateOf("") }

    if (showDayPicker) {
        val pickerState = rememberDateRangePickerState()
        DatePickerDialog(
            onDismissRequest = { showDayPicker = false },
            confirmButton = {
                TextButton(onClick = {
                    val s = pickerState.selectedStartDateMillis
                    val e = pickerState.selectedEndDateMillis
                    if (s != null && e != null) {
                        dayStart = millisToIso(s)
                        dayEnd = millisToIso(e)
                    }
                    showDayPicker = false
                }) { Text("确定") }
            },
            dismissButton = {
                TextButton(onClick = { showDayPicker = false }) { Text("取消") }
            },
        ) {
            DateRangePicker(state = pickerState, showModeToggle = false)
        }
        return
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = Dimens.pageGap, vertical = 8.dp)
            .clip(RoundedCornerShape(Dimens.radius))
            .background(colors.surface)
            .padding(16.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(label = "按日区间", selected = mode == "day", onClick = { mode = "day" })
            FilterChip(label = "按月区间", selected = mode == "month", onClick = { mode = "month" })
            FilterChip(label = "按年区间", selected = mode == "year", onClick = { mode = "year" })
        }

        if (mode == "day") {
            Spacer(Modifier.height(14.dp))
            SectionLabel("起止日期")
            Spacer(Modifier.height(8.dp))
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(Dimens.minTouch)
                    .clip(RoundedCornerShape(Dimens.radiusSm))
                    .background(colors.soft)
                    .clickable { showDayPicker = true },
                contentAlignment = Alignment.CenterStart,
            ) {
                Text(
                    if (dayStart.isEmpty()) "点这里选起止日期" else "$dayStart → $dayEnd",
                    color = if (dayStart.isEmpty()) colors.muted else colors.ink,
                    fontSize = 14.sp,
                    modifier = Modifier.padding(horizontal = 14.dp),
                )
            }
        }

        if (mode == "month" || mode == "year") {
            Spacer(Modifier.height(14.dp))
            SectionLabel(if (mode == "month") "起始月份" else "起始年份")
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(today.year - 2, today.year - 1, today.year).forEach { value ->
                    FilterChip("${value}", yearFrom == value) { yearFrom = value }
                }
            }
            Spacer(Modifier.height(10.dp))
            SectionLabel(if (mode == "month") "结束月份" else "结束年份")
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(today.year - 2, today.year - 1, today.year).forEach { value ->
                    FilterChip("${value}", yearTo == value) { yearTo = value }
                }
            }
            if (mode == "month") {
                Spacer(Modifier.height(10.dp))
                SectionLabel("起止月份（1–12）")
                Spacer(Modifier.height(8.dp))
                listOf((1..6).toList(), (7..12).toList()).forEach { row ->
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        row.forEach { value ->
                            val isFrom = monthFrom == value && yearFrom == yearTo
                            FilterChip("${value}月", isFrom) {
                                monthFrom = value
                                if (yearFrom == yearTo) monthTo = value
                            }
                        }
                    }
                    Spacer(Modifier.height(6.dp))
                }
            }
        }

        Spacer(Modifier.height(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Box(
                modifier = Modifier
                    .weight(1f)
                    .height(Dimens.minTouch)
                    .clip(RoundedCornerShape(Dimens.radiusSm))
                    .background(colors.soft)
                    .clickable(onClick = onDismiss),
                contentAlignment = Alignment.Center,
            ) {
                Text("取消", color = colors.muted, fontSize = 14.sp)
            }
            Box(
                modifier = Modifier
                    .weight(1f)
                    .height(Dimens.minTouch)
                    .clip(RoundedCornerShape(Dimens.radiusSm))
                    .background(colors.primary)
                    .clickable {
                        when (mode) {
                            "day" -> {
                                if (dayStart.isNotEmpty() && dayEnd.isNotEmpty()) {
                                    onPick(
                                        DateRange(
                                            mode = "custom",
                                            startDate = dayStart,
                                            endDate = dayEnd,
                                            label = "${dayStart.substring(5).replace('-', '/')}–${dayEnd.substring(5).replace('-', '/')}",
                                        )
                                    )
                                }
                            }
                            "month" -> {
                                val y1 = minOf(yearFrom, yearTo)
                                val y2 = maxOf(yearFrom, yearTo)
                                val m1 = if (y1 == yearFrom) monthFrom else monthTo
                                val m2 = if (y2 == yearTo) monthTo else monthFrom
                                onPick(
                                    DateRange(
                                        mode = "custom",
                                        startDate = "${y1}-${pad2(m1)}-01",
                                        endDate = "${y2}-${pad2(m2)}-${pad2(lastDayOf(y2, m2))}",
                                        label = if (y1 == y2) {
                                            "${y1} 年 ${m1}–${m2} 月"
                                        } else {
                                            "${y1}/${pad2(m1)}–${y2}/${pad2(m2)}"
                                        },
                                    )
                                )
                            }
                            else -> {
                                val y1 = minOf(yearFrom, yearTo)
                                val y2 = maxOf(yearFrom, yearTo)
                                onPick(
                                    DateRange(
                                        mode = "custom",
                                        startDate = "$y1-01-01",
                                        endDate = "$y2-12-31",
                                        label = if (y1 == y2) "${y1} 年" else "$y1–$y2 年",
                                    )
                                )
                            }
                        }
                    },
                contentAlignment = Alignment.Center,
            ) {
                Text("确定", color = colors.primaryInk, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            }
        }
    }
}

private fun pad2(value: Int): String = value.toString().padStart(2, '0')

private fun lastDayOf(year: Int, month: Int): Int =
    java.time.YearMonth.of(year, month).lengthOfMonth()

/** DatePicker 给的是 UTC 零点毫秒，转成 ISO 本地日期（不要用系统时区，会偏一天）。 */
private fun millisToIso(millis: Long): String =
    Instant.ofEpochMilli(millis).atZone(ZoneOffset.UTC).toLocalDate().toString()

/**
 * 单据筛选 chip（全部类型 / 销售 / 退货 / 拿货 / 退拿货）。
 *
 * 选中态原来是「蓝底」，未选中是「白底 + 没描边」—— 白底 chip 落在
 * 浅灰页面背景上几乎看不见边界，五个 chip 里只有蓝的那个有形状，
 * 其余四个像悬空的字。现在未选中改用 soft 灰底，谁都能看出「这是个按钮」，
 * 选中仍然是蓝底（蓝 = 当前生效）。
 */
@Composable
fun FilterChip(label: String, selected: Boolean, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    Box(
        modifier = Modifier
            .height(38.dp)
            .clip(RoundedCornerShape(19.dp))
            .background(if (selected) colors.primary else colors.soft)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            label,
            color = if (selected) colors.primaryInk else colors.ink,
            fontSize = 13.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
        )
    }
}
