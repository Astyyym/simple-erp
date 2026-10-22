package com.simpleerp.mobile.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
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
import com.simpleerp.mobile.ui.components.SectionLabel
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.util.formatCacheTime
import java.time.LocalDate

/**
 * 单据列表：类型筛选 + 日期筛选（年/月/日/全部），两者可叠加、互不覆盖。
 *
 * 日期换算规则**不在客户端算**：`date_mode` 与 `start_date`/`end_date` 直接交给服务端，
 * 由它复用桌面端 `_resolve_date_range()` 的换算。客户端只负责把用户点的东西
 * 变成这四个参数，避免出现「手机端按 31 天算、桌面端按月末算」这种分叉。
 */
@Composable
fun OrdersScreen(
    state: UiState<OrdersResponse>,
    orderType: String,
    dateRange: DateRange,
    customerName: String,
    onSelectType: (String) -> Unit,
    onSelectRange: (DateRange) -> Unit,
    onClearCustomer: () -> Unit,
    onOpenDocument: (orderType: String, id: Int) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    var showDateSheet by remember { mutableStateOf(false) }

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

        // ---- 日期按钮 ----
        // 顺序是「时间」在上、「类型」在下（用户 2026-10-21 指定调换）。
        // 理由：看单子先定时间范围（今天/全部），再看哪类单 —— 从粗到细才顺。
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = Dimens.pageGap, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(Dimens.radiusSm))
                    .background(colors.surface)
                    .clickable { showDateSheet = !showDateSheet }
                    .padding(horizontal = 14.dp, vertical = 10.dp),
            ) {
                // 不用 emoji：部分机型/字体把 📅 渲染成豆腐块（真机实测显示成「17」）。
                // 日期文案本身已经说明这是日期筛选，图标没有信息增量。
                Text(dateRange.label.ifEmpty { "今天" }, color = colors.ink, fontSize = 13.sp)
            }
            Spacer(Modifier.weight(1f))
            if (state is UiState.Ready) {
                Text("${state.value.documents.size} 张", color = colors.muted, fontSize = 12.sp)
            }
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

        if (showDateSheet) {
            DateRangePanel(
                current = dateRange,
                onPick = {
                    showDateSheet = false
                    onSelectRange(it)
                },
                onDismiss = { showDateSheet = false },
            )
        }

        when (state) {
            is UiState.Loading -> LoadingState()
            is UiState.Failed -> ErrorState(state.message, onAction = onRetry)
            UiState.NeedsPairing, UiState.NeedsSetup -> LoadingState()
            is UiState.Ready -> {
                val documents = state.value.documents
                LazyColumn(
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
                                hint = "点上面的日期按钮，可以改成「全部时间」看更早的单。",
                            )
                        }
                    }
                    // **一整张卡里分行**，对齐网页版 `orders/list.html` 的定稿结构。
                    // 原来每行一张白卡，175 张单排下去是一堵等大方块墙。
                    item {
                        ErpCard(padding = 0.dp) {
                            Column {
                                documents.forEachIndexed { index, doc ->
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

/**
 * 日期面板：年 / 月 / 日 / 全部 四档 + 四个快捷。
 *
 * **年份从左到右递增**（用户 2026-10-17 要求）：右撇子拇指活动区在右下，
 * 把最新的年份放最左等于每次都要横穿整个面板。
 */
@Composable
private fun DateRangePanel(
    current: DateRange,
    onPick: (DateRange) -> Unit,
    onDismiss: () -> Unit,
) {
    val colors = LocalErpColors.current
    val today = LocalDate.now()
    var mode by remember { mutableStateOf(current.mode.ifEmpty { "day" }) }
    var year by remember { mutableStateOf(if (current.year > 0) current.year else today.year) }
    var month by remember { mutableStateOf(if (current.month > 0) current.month else today.monthValue) }

    val years = listOf(today.year - 2, today.year - 1, today.year)

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = Dimens.pageGap, vertical = 8.dp)
            .clip(RoundedCornerShape(Dimens.radius))
            .background(colors.surface)
            .padding(16.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip("按日", mode == "day") { mode = "day" }
            FilterChip("按月", mode == "month") { mode = "month" }
            FilterChip("按年", mode == "year") { mode = "year" }
            FilterChip("全部", mode == "all") { mode = "all" }
        }

        if (mode == "year" || mode == "month") {
            Spacer(Modifier.height(14.dp))
            SectionLabel("年份")
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                years.forEach { value ->
                    FilterChip("${value}", year == value) { year = value }
                }
            }
        }

        if (mode == "month") {
            Spacer(Modifier.height(14.dp))
            SectionLabel("月份")
            Spacer(Modifier.height(8.dp))
            // 月份 12 个，分两行放，避免横向滚动的廉价感
            listOf((1..6).toList(), (7..12).toList()).forEach { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    row.forEach { value ->
                        FilterChip("${value}月", month == value) { month = value }
                    }
                }
                Spacer(Modifier.height(6.dp))
            }
        }

        Spacer(Modifier.height(14.dp))
        SectionLabel("快捷")
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip("今天", false) {
                val iso = today.toString()
                onPick(DateRange("day", iso, iso, today.year, today.monthValue, "今天"))
            }
            FilterChip("本月", false) {
                onPick(
                    DateRange(
                        mode = "month",
                        year = today.year,
                        month = today.monthValue,
                        label = "${today.year} 年 ${today.monthValue} 月",
                    )
                )
            }
            FilterChip("今年", false) {
                onPick(DateRange(mode = "year", year = today.year, label = "${today.year} 年"))
            }
            FilterChip("全部时间", false) {
                onPick(DateRange(mode = "all", label = "全部时间"))
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
                            "all" -> onPick(DateRange(mode = "all", label = "全部时间"))
                            "year" -> onPick(
                                DateRange(mode = "year", year = year, label = "$year 年")
                            )
                            "month" -> onPick(
                                DateRange(
                                    mode = "month",
                                    year = year,
                                    month = month,
                                    label = "$year 年 $month 月",
                                )
                            )
                            else -> {
                                // 按日：默认今天；用户改起止日期由服务端 `_resolve_date_range` 兜底
                                val iso = today.toString()
                                onPick(
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
                        }
                    },
                contentAlignment = Alignment.Center,
            ) {
                Text("确定", color = colors.primaryInk, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            }
        }
    }
}
