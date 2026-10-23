package com.simpleerp.mobile.ui.screens

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.lerp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.data.TodayEvent
import com.simpleerp.mobile.data.TodayOverview
import com.simpleerp.mobile.data.TodayType
import com.simpleerp.mobile.ui.UiState
import com.simpleerp.mobile.ui.components.AmountText
import com.simpleerp.mobile.ui.components.EmptyState
import com.simpleerp.mobile.ui.components.ErpCard
import com.simpleerp.mobile.ui.components.ErpHairline
import com.simpleerp.mobile.ui.components.ErrorState
import com.simpleerp.mobile.ui.components.LoadingState
import com.simpleerp.mobile.ui.components.OfflineBanner
import com.simpleerp.mobile.ui.components.Refreshable
import com.simpleerp.mobile.ui.components.SectionLabel
import com.simpleerp.mobile.ui.components.TypeBadge
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.util.formatCacheTime

/**
 * 今日概览（首屏）。
 *
 * 口径照抄决策文档 §5.1，**不做四类相加的总计**：
 * - 按 4 类分组（销售 / 退货 / 拿货 / 退拿货），某类为 0 也保留该行；
 * - 只给「卖货净额」「进货净额」两段小计；
 * - 不显示任何成本或利润数字。
 *
 * 这两条不是审美选择：四类相加的数既不是今天收到的钱、也不是今天的利润，
 * 符号还与余额口径相反。桌面端从来没有这个数，手机端也不该发明它。
 */
@Composable
fun TodayScreen(
    state: UiState<TodayOverview>,
    /** 列表滚动状态由上层持有（切 tab / 返回详情不丢位置，2026-10-22 修）。 */
    listState: LazyListState,
    refreshing: Boolean,
    onRefresh: () -> Unit,
    onRetry: () -> Unit,
    onOpenDocument: (orderType: String, id: Int) -> Unit,
    onShowTypeOrders: (orderType: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    when (state) {
        is UiState.Loading -> LoadingState(modifier)
        is UiState.Failed -> ErrorState(state.message, modifier, onAction = onRetry)
        UiState.NeedsPairing, UiState.NeedsSetup -> LoadingState(modifier)
        is UiState.Ready -> TodayContent(
            overview = state.value,
            fromCache = state.fromCache,
            fetchedAt = state.fetchedAt,
            listState = listState,
            refreshing = refreshing,
            onRefresh = onRefresh,
            onRetry = onRetry,
            onOpenDocument = onOpenDocument,
            onShowTypeOrders = onShowTypeOrders,
            modifier = modifier,
        )
    }
}

/** 页头展开态的高度。抽成常量，好让折叠动画的起止值一目了然。 */
private val HeaderExpandedHeight: Dp = 88.dp
private val HeaderCollapsedHeight: Dp = 46.dp

/** 折叠完成所需的滚动距离（px）。越小越「一滑就收」。 */
private const val HeaderCollapseDistancePx = 140f

@Composable
private fun TodayContent(
    overview: TodayOverview,
    fromCache: Boolean,
    fetchedAt: Long,
    listState: LazyListState,
    refreshing: Boolean,
    onRefresh: () -> Unit,
    onRetry: () -> Unit,
    onOpenDocument: (String, Int) -> Unit,
    onShowTypeOrders: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    val saleTypes = overview.types.filter { it.type == "sale" || it.type == "return" }
    val purchaseTypes = overview.types.filter {
        it.type == "purchase" || it.type == "purchase_return"
    }

    /*
     * 折叠进度：0 = 回顶展开（现状），1 = 滑动后收成一行。
     *
     * 为什么把页头放在列表**外面**、而不是做成 stickyHeader：
     * 页头在列表外时，列表自身的滚动偏移就能直接驱动折叠 —— 不需要 nestedScroll，
     * 也不会出现「stickyHeader 被顶掉一截再弹回」的抖动。
     * 用户要的是「滑动时顶部固定、回顶时恢复展开」，这正是固定栏 + 高度动画。
     */
    val collapseProgress by remember {
        derivedStateOf {
            if (listState.firstVisibleItemIndex > 0) {
                1f
            } else {
                (listState.firstVisibleItemScrollOffset / HeaderCollapseDistancePx).coerceIn(0f, 1f)
            }
        }
    }
    val animatedProgress by animateFloatAsState(
        targetValue = collapseProgress,
        animationSpec = tween(durationMillis = 180),
        label = "todayHeaderCollapse",
    )

    Column(modifier = modifier.fillMaxSize()) {
        TodayHeader(
            overview = overview,
            progress = animatedProgress,
        )

        // weight(1f)：页头固定后，列表要吃掉**剩余**高度；不给权重的话
        // PullToRefreshBox 在 Column 里会按内容测高，列表可能只显示一屏内的几行。
        Refreshable(
            refreshing = refreshing,
            onRefresh = onRefresh,
            modifier = Modifier.weight(1f),
        ) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(
                    start = Dimens.pageGap,
                    end = Dimens.pageGap,
                    top = 6.dp,
                    bottom = 28.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                if (fromCache) {
                    item { OfflineBanner(formatCacheTime(fetchedAt)) }
                }

                // ---- 卖货 / 进货：两张平行卡（净额 + 四类明细合并） ----
                // 2026-10-23 用户要求：原来「净额两张卡 + 卖货一节 + 进货一节」占掉
                // 大半屏，把「今天开过的单」挤到看不见。合并成左右两张卡后，
                // 高度从约 4 行降到 2 行，下方的单据时间轴拿到更多空间。
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        GroupCard(
                            title = "卖货",
                            netText = overview.netSaleText,
                            netCents = overview.netSaleCents,
                            // 卖货净额 = 收入方向，用绿色（用户 2026-10-23 指定）。
                            netTint = colors.success,
                            entries = saleTypes,
                            onShowTypeOrders = onShowTypeOrders,
                            modifier = Modifier.weight(1f),
                        )
                        GroupCard(
                            title = "进货",
                            netText = overview.netPurchaseText,
                            netCents = overview.netPurchaseCents,
                            // 进货净额 = 支出方向，用红色（用户 2026-10-23 指定）。
                            netTint = colors.danger,
                            entries = purchaseTypes,
                            onShowTypeOrders = onShowTypeOrders,
                            modifier = Modifier.weight(1f),
                        )
                    }
                }

                // ---- 时间轴 ----
                if (overview.events.isEmpty()) {
                    item { EmptyState("今天还没有开单", "开了单之后这里会按时间倒序列出。") }
                } else {
                    item { SectionLabel("今天开过的单", modifier = Modifier.padding(top = 2.dp)) }
                    items(overview.events, key = { "${it.orderType}-${it.sourceId}" }) { event ->
                        EventRow(event = event, onClick = { onOpenDocument(event.orderType, event.sourceId) })
                    }
                }
            }
        }
    }
}

/**
 * 今日页头：**展开 ↔ 单行 的折叠动画**（用户 2026-10-23 要求）。
 *
 * 展开态（回顶时）保持原来的三段式：`今天` / 大日期 + 星期 / 共 N 张单据。
 * 折叠态（上下滑动时）压成一行：`10月23日 周五 共 0 张单据`，
 * 这样一屏能多露出下面一两张单据。
 *
 * 用高度 lerp + 两层交叉淡入淡出，而不是 `AnimatedContent`：
 * 后者切换时会换一套布局，滚动过程中来回跨阈值会「跳」，肉眼像卡顿。
 */
@Composable
private fun TodayHeader(overview: TodayOverview, progress: Float) {
    val colors = LocalErpColors.current
    val height = lerp(HeaderExpandedHeight, HeaderCollapsedHeight, progress)
    val expandedAlpha = (1f - progress * 1.6f).coerceIn(0f, 1f)
    val collapsedAlpha = ((progress - 0.45f) / 0.55f).coerceIn(0f, 1f)

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(height)
            .background(colors.bg)
            .padding(horizontal = Dimens.pageGap),
    ) {
        // ---- 展开态 ----
        Column(
            modifier = Modifier
                .align(Alignment.CenterStart)
                .alpha(expandedAlpha),
        ) {
            Text(
                text = "今天",
                color = colors.muted,
                fontSize = 12.5.sp,
                fontWeight = FontWeight.Medium,
            )
            Spacer(Modifier.height(3.dp))
            Row(verticalAlignment = Alignment.Bottom) {
                Text(
                    text = overview.dateLabel,
                    color = colors.ink,
                    fontSize = 22.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = overview.weekday,
                    color = colors.muted,
                    fontSize = 13.sp,
                    modifier = Modifier.padding(bottom = 3.dp),
                )
            }
            Spacer(Modifier.height(4.dp))
            Text(
                text = "共 ${overview.documentCount} 张单据",
                color = colors.muted,
                fontSize = 12.5.sp,
            )
        }

        // ---- 折叠态：一行 ----
        Row(
            modifier = Modifier
                .align(Alignment.CenterStart)
                .alpha(collapsedAlpha),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = overview.dateLabel,
                color = colors.ink,
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.width(7.dp))
            Text(text = overview.weekday, color = colors.muted, fontSize = 12.5.sp)
            Spacer(Modifier.width(8.dp))
            Text(
                text = "共 ${overview.documentCount} 张单据",
                color = colors.muted,
                fontSize = 12.5.sp,
            )
        }
    }
}

/**
 * 卖货 / 进货 平行卡：**标题 + 净额 + 该方向的两类明细**。
 *
 * 净额是这张卡的主角（20sp Bold + 指定色），下面的明细行是小字，
 * 层级一眼可分。合并前净额和明细是两组独立卡片，占高太多。
 */
@Composable
private fun GroupCard(
    title: String,
    netText: String,
    netCents: Long,
    netTint: androidx.compose.ui.graphics.Color,
    entries: List<TodayType>,
    onShowTypeOrders: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    ErpCard(modifier = modifier, padding = 0.dp) {
        Column(modifier = Modifier.padding(horizontal = 14.dp, vertical = 13.dp)) {
            Text(title, color = colors.muted, fontSize = 11.5.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(6.dp))
            AmountText(
                text = netText,
                cents = netCents,
                size = 20,
                weight = FontWeight.Bold,
                tint = netTint,
            )
            Spacer(Modifier.height(10.dp))
            entries.forEachIndexed { index, entry ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(6.dp))
                        .clickable { onShowTypeOrders(entry.type) }
                        .padding(vertical = 5.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = entry.label,
                        color = colors.ink,
                        fontSize = 12.5.sp,
                        maxLines = 1,
                    )
                    Spacer(Modifier.weight(1f))
                    Text(
                        text = "${entry.count} 单",
                        color = colors.muted,
                        fontSize = 11.sp,
                        maxLines = 1,
                    )
                }
                if (index != entries.lastIndex) {
                    ErpHairline(Modifier.padding(vertical = 1.dp))
                }
            }
        }
    }
}

@Composable
private fun EventRow(event: TodayEvent, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    ErpCard(onClick = onClick, padding = 15.dp) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TypeBadge(event.typeLabel, event.orderType)
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = event.customerName,
                        color = colors.ink,
                        fontSize = 14.5.sp,
                        maxLines = 1,
                    )
                }
                Spacer(Modifier.height(5.dp))
                Text(
                    text = "${event.time}　${event.orderNo}",
                    color = colors.muted,
                    fontSize = 11.5.sp,
                )
            }
            Spacer(Modifier.width(10.dp))
            AmountText(text = event.amountText, cents = event.amountCents, size = 15)
        }
    }
}
