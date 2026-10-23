package com.simpleerp.mobile.ui.screens

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.lerp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.data.DateRange
import com.simpleerp.mobile.data.OrdersResponse
import com.simpleerp.mobile.ui.UiState
import com.simpleerp.mobile.ui.components.EmptyState
import com.simpleerp.mobile.ui.components.ErpCard
import com.simpleerp.mobile.ui.components.MonthCalendar
import com.simpleerp.mobile.ui.components.ErpHairline
import com.simpleerp.mobile.ui.components.ErrorState
import com.simpleerp.mobile.ui.components.LoadingState
import com.simpleerp.mobile.ui.components.OfflineBanner
import com.simpleerp.mobile.ui.components.RangeGridCell
import com.simpleerp.mobile.ui.components.Refreshable
import com.simpleerp.mobile.ui.components.SectionLabel
import com.simpleerp.mobile.ui.components.listRowBorder
import com.simpleerp.mobile.ui.components.listRowShape
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.util.RangeLabels
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.YearMonth

/**
 * 单据列表：类型筛选 + 时间筛选，两者可叠加、互不覆盖。
 *
 * 时间换算规则**不在客户端算**：`date_mode` 与 `start_date`/`end_date` 直接交给服务端，
 * 由它复用桌面端 `_resolve_date_range()` 的换算。客户端只负责把用户点的东西
 * 变成这几个参数，避免出现「手机端按 31 天算、桌面端按月末算」这种分叉。
 *
 * ## 2026-10-23 第二次改版（用户四条反馈）
 *
 * 1. **顶部筛选项太多**：原来「今天/当周/当月/当年/更多筛选」五个 chip + 一行类型
 *    chip，两行就把单据列表顶掉小半屏。现在时间收成**一个按钮**（显示当前范围，
 *    点开面板选），顶部只剩「时间按钮 + N 张」和类型一行。
 * 2. **数量被挤出页面**：原来「8 张」挤在时间 chip 横滚条的末尾，chip 一多就被
 *    推到屏幕外。现在数量放在第一行右侧、**不参与横滚**，任何时候都在。
 * 3. **上下滑动时顶部要固定 + 动画**：筛选条提到列表外面固定，列表滚动偏移驱动
 *    `animateFloatAsState`，回顶展开两行、滑动压成一行 `10 月　全部类型　36 张`
 *    （1.4.6 起不带 ↑ 箭头，点整行仍会滚回顶部展开）。
 *    手法与首页 `TodayHeader` 完全一致（高度 lerp + 双层交叉淡入），
 *    点折叠态那一行会滚回顶部把筛选条展开。
 * 4. **「更多筛选」交互重做**：改成底部面板，按「1 快捷选择 / 2 自定义区间」的
 *    顺序排布；日历不再是 material3 的 Google 原生日历，换成自绘中文月历
 *    （周一起始、起止实心蓝圆、区间浅蓝底，见 `ErpCalendar.kt`）。
 *    面板自己可滚，再多的选项也只会滚面板，不会把列表挤走。
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
    var showTimeSheet by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    /*
     * 折叠进度：0 = 回顶展开（时间按钮 + 类型行），1 = 压成一行摘要。
     *
     * 页头放在列表**外面**，列表自己的滚动偏移就能直接驱动折叠 ——
     * 不需要 nestedScroll，也不会出现 stickyHeader「顶掉一截再弹回」的抖动。
     */
    val collapseProgress by remember {
        derivedStateOf {
            if (listState.firstVisibleItemIndex > 0) {
                1f
            } else {
                (listState.firstVisibleItemScrollOffset / FilterCollapseDistancePx).coerceIn(0f, 1f)
            }
        }
    }
    val animatedProgress by animateFloatAsState(
        targetValue = collapseProgress,
        animationSpec = tween(durationMillis = 180),
        label = "ordersFilterCollapse",
    )

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

        OrdersFilterBar(
            dateLabel = dateRange.displayLabel(),
            orderType = orderType,
            count = (state as? UiState.Ready)?.value?.documents?.size,
            progress = animatedProgress,
            onSelectType = onSelectType,
            onOpenTime = { showTimeSheet = true },
            onExpand = { scope.launch { listState.animateScrollToItem(0) } },
        )

        if (showTimeSheet) {
            TimeFilterSheet(
                current = dateRange,
                onDismiss = { showTimeSheet = false },
                onPick = {
                    showTimeSheet = false
                    onSelectRange(it)
                },
            )
        }

        when (state) {
            is UiState.Loading -> LoadingState()
            is UiState.Failed -> ErrorState(state.message, onAction = onRetry)
            UiState.NeedsPairing, UiState.NeedsSetup -> LoadingState()
            is UiState.Ready -> {
                val documents = state.value.documents
                Refreshable(
                    refreshing = refreshing,
                    onRefresh = onRefresh,
                    // weight(1f)：筛选条固定后，列表要吃掉**剩余**高度。
                    // 不给权重的话 PullToRefreshBox 会按内容测高，长列表在 Column 里会被裁。
                    modifier = Modifier.weight(1f),
                ) {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxWidth(),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(
                            start = Dimens.pageGap,
                            end = Dimens.pageGap,
                            top = 8.dp,
                            bottom = 28.dp,
                        ),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        if (state.fromCache) {
                            item { OfflineBanner(com.simpleerp.mobile.ui.util.formatCacheTime(state.fetchedAt)) }
                        }
                        if (documents.isEmpty()) {
                            item {
                                EmptyState(
                                    title = "这段时间没有单据",
                                    hint = "点上面的时间按钮，可以改成自定义区间看更早的单。",
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
                                    "只显示了前 200 张，缩小时间范围可以看到更早的。",
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
 * 展开态高度：**与首页页头完全一致**（`TodayScreen` 的 `HeaderExpandedHeight`）。
 *
 * 用户 2026-10-23 要求「单据页顶部固定高度同第一页，字体大小也要相同」——
 * 两个页面顶部都在干同一件事（说明「你现在看的是哪一段」），
 * 一高一矮、一大一小会让人以为进了两个 App。
 */
private val FilterExpandedHeight = 88.dp

/** 折叠态高度：同首页页头的 `HeaderCollapsedHeight`。 */
private val FilterCollapsedHeight = 46.dp

/** 折叠完成所需的滚动距离（px）。与首页页头同一量级，手感一致。 */
private const val FilterCollapseDistancePx = 140f

/**
 * 固定筛选条：展开 ↔ 折叠一行。
 *
 * 展开态：
 * ```
 * 10 月　时间筛选 ▾　36 张
 * [全部类型][销售][退货][拿货][退拿货]
 * ```
 * 折叠态：`10 月　全部类型　36 张`（整行可点，点了滚回顶部展开；不带箭头）。
 *
 * 字号/高度照抄首页页头：展开态数值 22sp SemiBold、说明字 12.5sp，
 * 折叠态数值 16sp SemiBold、其余 12.5sp。两页看起来才是一套东西。
 *
 * 为什么数量不放在类型行：类型 chip 在 360dp 窄屏上正好铺满，再塞「36 张」
 * 就会把最后一个 chip 和数量一起挤出去 —— 这正是用户报的那个问题。
 * 数量跟在数值后面、与说明同排靠左，窄屏也不会丢。
 */
@Composable
private fun OrdersFilterBar(
    dateLabel: String,
    orderType: String,
    count: Int?,
    progress: Float,
    onSelectType: (String) -> Unit,
    onOpenTime: () -> Unit,
    onExpand: () -> Unit,
) {
    val colors = LocalErpColors.current
    val height = lerp(FilterExpandedHeight, FilterCollapsedHeight, progress)
    val expandedAlpha = (1f - progress * 1.6f).coerceIn(0f, 1f)
    val collapsedAlpha = ((progress - 0.45f) / 0.55f).coerceIn(0f, 1f)

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(height)
            .background(colors.bg),
    ) {
        /*
         * 只有「看得见」的那一层才进入组合。
         *
         * 原来两层都常驻、只用 alpha 交叉淡入 —— 透明的折叠行仍然**可以接收点击**，
         * 它盖在展开态的时间按钮上（高度 46dp、居中），于是点时间按钮没反应：
         * 事件被 alpha=0 的那一行吃掉（2026-10-23 实测点了两次都不弹面板）。
         * 按 alpha 阈值决定是否组合，既保留淡入淡出，又不会留下看不见的点击层。
         */
        if (expandedAlpha > 0.02f) {
            // ---- 展开态：`10 月　时间筛选 ▾　36 张`（全部靠左）----
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .align(Alignment.TopStart)
                    .alpha(expandedAlpha),
            ) {
                /*
                 * 顺序按用户 2026-10-23 指定：**数值在前、说明在后、数量紧跟**，整行靠左。
                 *
                 * 原来是「时间筛选 10 月 …… 36 张」，数量被 weight 推到屏幕最右边：
                 * 眼睛要先扫过一整行空白才找到数量，而且数值（真正要读的东西）被挤到
                 * 说明词后面。现在数值第一眼就看到，`时间筛选` 退成它右边的小字注脚，
                 * `36 张` 紧跟在后面 —— 一行读下来是「十月 · 时间筛选 · 36 张」。
                 */
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(10.dp))
                        .clickable(onClick = onOpenTime)
                        .padding(start = Dimens.pageGap, end = Dimens.pageGap, top = 5.dp, bottom = 1.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = dateLabel,
                        color = colors.ink,
                        fontSize = 22.sp,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Spacer(Modifier.width(7.dp))
                    // 「时间筛选」是这一行的**说明**，不是标题：它跟在数值后面做注脚。
                    // （原来叫「日期」，但这里筛的还有整月/整年/自定义区间。）
                    Text(
                        "时间筛选",
                        color = colors.muted,
                        fontSize = 12.5.sp,
                        fontWeight = FontWeight.Medium,
                    )
                    Spacer(Modifier.width(3.dp))
                    Text("▾", color = colors.muted, fontSize = 11.sp)
                    if (count != null) {
                        Spacer(Modifier.width(10.dp))
                        Text("$count 张", color = colors.muted, fontSize = 12.5.sp)
                    }
                }
                // 类型行：五个 chip 在 360dp 上刚好铺满，更窄就滚动**这一行自己**。
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState())
                        .padding(start = Dimens.pageGap, end = Dimens.pageGap, top = 1.dp, bottom = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    ORDER_TYPE_OPTIONS.forEach { (value, label) ->
                        FilterChip(
                            label = label,
                            selected = orderType == value,
                            height = 36.dp,
                            onClick = { onSelectType(value) },
                        )
                    }
                }
            }
        }

        if (collapsedAlpha > 0.02f) {
            // ---- 折叠态：一行摘要（点了回顶展开筛选条）----
            // 字号/高度与首页折叠态一致：数值 16sp SemiBold，其余 12.5sp。
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(FilterCollapsedHeight)
                    .align(Alignment.CenterStart)
                    .alpha(collapsedAlpha)
                    .clickable(onClick = onExpand)
                    .padding(horizontal = Dimens.pageGap),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // 与展开态同一个顺序、同样靠左：`10 月　全部类型　36 张`。
                // 数量不推到最右边——展开/折叠切换时它不该「跳一下」。
                Text(
                    text = dateLabel,
                    color = colors.ink,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                )
                Spacer(Modifier.width(7.dp))
                Text(
                    text = typeLabelText(orderType),
                    color = colors.muted,
                    fontSize = 12.5.sp,
                    maxLines = 1,
                )
                if (count != null) {
                    Spacer(Modifier.width(10.dp))
                    Text("$count 张", color = colors.muted, fontSize = 12.5.sp)
                }
                // 末尾原来挂了一个 ↑ 提示「点这一行回顶」，用户 2026-10-23 要求删掉：
                // 折叠态本身就是「已经滚下去了」的证据，再挂箭头反而像多了一个按钮。
                // 点整行回顶的行为保留（只是不再明示）。
            }
        }
    }
}

/** 类型选项（value 传服务端，label 给用户看）。顺序沿用用户 2026-10-21 定的排列。 */
private val ORDER_TYPE_OPTIONS = listOf(
    "" to "全部类型",
    "sale" to "销售",
    "return" to "退货",
    "purchase" to "拿货",
    "purchase_return" to "退拿货",
)

/** 当前时间范围在按钮上的文案（纯函数在 [RangeLabels]，可单测）。 */
internal fun DateRange.displayLabel(): String = RangeLabels.buttonLabel(this)

internal fun typeLabelText(orderType: String): String = RangeLabels.typeLabel(orderType)

// ---------------------------------------------------------------------------
// 时间筛选面板（底部弹出）
// ---------------------------------------------------------------------------

/**
 * 「筛选时间」底部面板。
 *
 * 交互顺序**显式分两步**（用户 2026-10-23 反馈「操作顺序不清晰」）：
 * 1. 快捷选择 —— 今天 / 本周 / 本月 / 本年，一点即生效并关闭；
 * 2. 自定义区间 —— 先选口径（按日/按月/按年），再在下面是日历或网格里点起止。
 *
 * 面板自己可滚（`verticalScroll` + 最大高度），所以选项再多也只是面板内部滚动，
 * 不会像原来的内联展开那样把「N 张」和列表挤出屏幕。
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun TimeFilterSheet(
    current: DateRange,
    onDismiss: () -> Unit,
    onPick: (DateRange) -> Unit,
) {
    val colors = LocalErpColors.current
    val today = LocalDate.now()
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    // 进入面板时尽量还原「当前生效的自定义区间」，让用户接着改而不是从零开始。
    val parsed = remember(current) {
        val s = runCatching { LocalDate.parse(current.startDate) }.getOrNull()
        val e = runCatching { LocalDate.parse(current.endDate) }.getOrNull()
        Triple(current.mode, s, e)
    }
    var mode by remember { mutableStateOf(if (parsed.first == "month" || parsed.first == "year") parsed.first else "day") }
    var dayStart by remember { mutableStateOf(parsed.second) }
    var dayEnd by remember { mutableStateOf(parsed.third) }
    var calMonth by remember { mutableStateOf(YearMonth.from(parsed.second ?: today)) }
    var monthYear by remember { mutableStateOf((parsed.second ?: today).year) }
    var monthStart by remember { mutableStateOf(parsed.second?.let { YearMonth.from(it) }) }
    var monthEnd by remember { mutableStateOf(parsed.third?.let { YearMonth.from(it) }) }
    var yearStart by remember { mutableStateOf(parsed.second?.year) }
    var yearEnd by remember { mutableStateOf(parsed.third?.year) }
    var yearAnchor by remember { mutableStateOf((parsed.second ?: today).year) }

    val result: DateRange? = when (mode) {
        "day" -> {
            val s = dayStart
            val e = dayEnd
            if (s != null && e != null) {
                DateRange(
                    mode = "custom",
                    startDate = s.toString(),
                    endDate = e.toString(),
                    label = RangeLabels.days(s, e),
                )
            } else null
        }
        "month" -> {
            val s = monthStart
            val e = monthEnd
            if (s != null && e != null) {
                val lo = minOf(s, e)
                val hi = maxOf(s, e)
                DateRange(
                    mode = "custom",
                    startDate = "${lo.year}-${pad2(lo.monthValue)}-01",
                    endDate = "${hi.year}-${pad2(hi.monthValue)}-${pad2(lastDayOf(hi.year, hi.monthValue))}",
                    label = RangeLabels.months(s, e),
                )
            } else null
        }
        else -> {
            val s = yearStart
            val e = yearEnd
            if (s != null && e != null) {
                val y1 = minOf(s, e)
                val y2 = maxOf(s, e)
                DateRange(
                    mode = "custom",
                    startDate = "$y1-01-01",
                    endDate = "$y2-12-31",
                    label = RangeLabels.years(y1, y2),
                )
            } else null
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = colors.surface,
        scrimColor = androidx.compose.ui.graphics.Color.Black.copy(alpha = 0.42f),
        shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(max = 620.dp)
                .verticalScroll(rememberScrollState())
                .navigationBarsPadding()
                .padding(start = Dimens.pageGap, end = Dimens.pageGap, bottom = 16.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("筛选时间", color = colors.ink, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.weight(1f))
                Text(
                    "关闭",
                    color = colors.muted,
                    fontSize = 12.5.sp,
                    modifier = Modifier.clickable(onClick = onDismiss).padding(6.dp),
                )
            }

            // ---- 步骤 1：快捷 ----
            SectionLabel("1. 快捷选择", modifier = Modifier.padding(top = 14.dp))
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(top = 10.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                FilterChip("今天", current.mode == "day") {
                    val iso = today.toString()
                    onPick(
                        DateRange(
                            mode = "day",
                            startDate = iso,
                            endDate = iso,
                            year = today.year,
                            month = today.monthValue,
                            label = "今天",
                        )
                    )
                }
                FilterChip("本周", current.mode == "week") {
                    // 起止日期由服务端按 ISO 周算，这里只传 mode。
                    onPick(DateRange(mode = "week", label = "当周"))
                }
                FilterChip("本月", current.mode == "month") {
                    onPick(
                        DateRange(
                            mode = "month",
                            year = today.year,
                            month = today.monthValue,
                            label = "当月",
                        )
                    )
                }
                FilterChip("本年", current.mode == "year") {
                    onPick(DateRange(mode = "year", year = today.year, label = "当年"))
                }
            }

            // ---- 步骤 2：自定义 ----
            SectionLabel("2. 自定义区间", modifier = Modifier.padding(top = 18.dp))
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(top = 10.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                FilterChip("按日", mode == "day") { mode = "day" }
                FilterChip("按月", mode == "month") { mode = "month" }
                FilterChip("按年", mode == "year") { mode = "year" }
            }

            Spacer(Modifier.height(12.dp))

            when (mode) {
                "day" -> {
                    Text(
                        if (dayStart == null) "先点开始日期，再点结束日期" else "再点结束日期（点已选起点可重选）",
                        color = colors.muted,
                        fontSize = 11.5.sp,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                    MonthCalendar(
                        month = calMonth,
                        start = dayStart,
                        end = dayEnd,
                        today = today,
                        onPick = { date ->
                            when {
                                dayStart == null -> {
                                    dayStart = date
                                    dayEnd = null
                                }
                                dayEnd == null -> {
                                    if (date.isBefore(dayStart!!)) {
                                        dayStart = date
                                    } else {
                                        dayEnd = date
                                    }
                                }
                                else -> {
                                    // 已经选完一段，再点就重新开始，手感像系统日历。
                                    dayStart = date
                                    dayEnd = null
                                }
                            }
                        },
                        onMonthChange = { calMonth = it },
                    )
                }

                "month" -> {
                    Text(
                        if (monthStart == null) "点月份选起始月，再点一次选结束月" else "再点一个月份作为结束月",
                        color = colors.muted,
                        fontSize = 11.5.sp,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                    YearStepper(year = monthYear, onYear = { monthYear = it })
                    MonthGrid(
                        year = monthYear,
                        start = monthStart,
                        end = monthEnd,
                        today = today,
                        onPick = { ym ->
                            when {
                                monthStart == null -> {
                                    monthStart = ym
                                    monthEnd = null
                                }
                                monthEnd == null -> {
                                    if (ym.isBefore(monthStart)) monthStart = ym else monthEnd = ym
                                }
                                else -> {
                                    monthStart = ym
                                    monthEnd = null
                                }
                            }
                        },
                    )
                }

                else -> {
                    Text(
                        if (yearStart == null) "点年份选起始年，再点一次选结束年" else "再点一个年份作为结束年",
                        color = colors.muted,
                        fontSize = 11.5.sp,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                    YearStepper(year = yearAnchor, onYear = { yearAnchor = it })
                    YearGrid(
                        anchor = yearAnchor,
                        start = yearStart,
                        end = yearEnd,
                        today = today,
                        onPick = { year ->
                            when {
                                yearStart == null -> {
                                    yearStart = year
                                    yearEnd = null
                                }
                                yearEnd == null -> {
                                    if (year < yearStart!!) yearStart = year else yearEnd = year
                                }
                                else -> {
                                    yearStart = year
                                    yearEnd = null
                                }
                            }
                        },
                    )
                }
            }

            // ---- 已选摘要 + 确认 ----
            Spacer(Modifier.height(16.dp))
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(Dimens.radiusSm))
                    .background(colors.soft)
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("已选", color = colors.muted, fontSize = 11.5.sp)
                Spacer(Modifier.width(8.dp))
                Text(
                    text = result?.label ?: "还没选完",
                    color = if (result != null) colors.ink else colors.muted,
                    fontSize = 13.sp,
                    fontWeight = if (result != null) FontWeight.SemiBold else FontWeight.Normal,
                )
            }

            Spacer(Modifier.height(12.dp))
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
                        .background(if (result != null) colors.primary else colors.muted.copy(alpha = 0.3f))
                        .clickable(enabled = result != null) { result?.let(onPick) },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        "确定",
                        color = colors.primaryInk,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Medium,
                    )
                }
            }
        }
    }
}

/** 年份翻页器：`‹ 2026 年 ›`（月/年网格共用）。 */
@Composable
private fun YearStepper(year: Int, onYear: (Int) -> Unit) {
    val colors = LocalErpColors.current
    Row(
        modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .height(32.dp)
                .width(40.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(colors.soft)
                .clickable { onYear(year - 1) },
            contentAlignment = Alignment.Center,
        ) { Text("‹", color = colors.ink, fontSize = 15.sp) }
        Text(
            "$year 年",
            color = colors.ink,
            fontSize = 13.5.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.weight(1f),
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
        Box(
            modifier = Modifier
                .height(32.dp)
                .width(40.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(colors.soft)
                .clickable { onYear(year + 1) },
            contentAlignment = Alignment.Center,
        ) { Text("›", color = colors.ink, fontSize = 15.sp) }
    }
}

/** 12 个月网格（3 列 × 4 行）。 */
@Composable
private fun MonthGrid(
    year: Int,
    start: YearMonth?,
    end: YearMonth?,
    today: LocalDate,
    onPick: (YearMonth) -> Unit,
) {
    val lo = start
    val hi = end
    Column {
        (0 until 4).forEach { row ->
            Row(Modifier.fillMaxWidth()) {
                (1..3).forEach { col ->
                    val m = row * 3 + col
                    val ym = YearMonth.of(year, m)
                    val selected = lo == ym || hi == ym
                    val inRange = lo != null && hi != null &&
                        ym.isAfter(lo) && ym.isBefore(hi)
                    RangeGridCell(
                        label = "$m 月",
                        selected = selected,
                        inRange = inRange,
                        enabled = true,
                        onClick = { onPick(ym) },
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

/** 年份网格（4 列，以 [anchor] 为基准向前排 12 年）。 */
@Composable
private fun YearGrid(
    anchor: Int,
    start: Int?,
    end: Int?,
    today: LocalDate,
    onPick: (Int) -> Unit,
) {
    val first = anchor - 7
    Column {
        (0 until 3).forEach { row ->
            Row(Modifier.fillMaxWidth()) {
                (0..3).forEach { col ->
                    val y = first + row * 4 + col
                    val selected = start == y || end == y
                    val inRange = start != null && end != null && y > start && y < end
                    RangeGridCell(
                        label = "$y",
                        selected = selected,
                        inRange = inRange,
                        // 未来年份没有单据，灰掉。
                        enabled = y <= today.year,
                        onClick = { onPick(y) },
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

private fun pad2(value: Int): String = value.toString().padStart(2, '0')

private fun lastDayOf(year: Int, month: Int): Int = YearMonth.of(year, month).lengthOfMonth()

/**
 * 单据筛选 chip（全部类型 / 销售 / 退货 / 拿货 / 退拿货）。
 *
 * 选中态原来是「蓝底」，未选中是「白底 + 没描边」—— 白底 chip 落在
 * 浅灰页面背景上几乎看不见边界，五个 chip 里只有蓝的那个有形状，
 * 其余四个像悬空的字。现在未选中改用 soft 灰底，谁都能看出「这是个按钮」，
 * 选中仍然是蓝底（蓝 = 当前生效）。
 *
 * [height] 可调：单据页顶部要把整条控制在 88dp（与首页页头同高），
 * 类型行只能给 36dp；「时间筛选」面板里的快捷项仍用默认 38dp。
 *
 * `onClick` 必须是**最后一个参数**：调用处普遍写成尾随 lambda `FilterChip("销售", sel) { … }`，
 * 把 height 放在它后面会让 lambda 绑到 Dp 上，编译器只报一句看不懂的
 * 「No value passed for parameter height」。
 */
@Composable
fun FilterChip(
    label: String,
    selected: Boolean,
    height: androidx.compose.ui.unit.Dp = 38.dp,
    onClick: () -> Unit,
) {
    val colors = LocalErpColors.current
    Box(
        modifier = Modifier
            .height(height)
            .clip(RoundedCornerShape(height / 2))
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
