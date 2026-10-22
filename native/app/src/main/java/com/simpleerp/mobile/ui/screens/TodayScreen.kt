package com.simpleerp.mobile.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
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
            onRetry = onRetry,
            onOpenDocument = onOpenDocument,
            onShowTypeOrders = onShowTypeOrders,
            modifier = modifier,
        )
    }
}

@Composable
private fun TodayContent(
    overview: TodayOverview,
    fromCache: Boolean,
    fetchedAt: Long,
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

    LazyColumn(
        modifier = modifier.fillMaxWidth(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(
            start = Dimens.pageGap,
            end = Dimens.pageGap,
            top = 12.dp,
            bottom = 28.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (fromCache) {
            item { OfflineBanner(formatCacheTime(fetchedAt)) }
        }

        // ---- 页头：日期 + 单数 ----
        item {
            Column(modifier = Modifier.padding(top = 4.dp, bottom = 2.dp)) {
                Text(
                    text = "今天",
                    color = colors.muted,
                    fontSize = 12.5.sp,
                    fontWeight = FontWeight.Medium,
                )
                Spacer(Modifier.height(4.dp))
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
                Spacer(Modifier.height(6.dp))
                Text(
                    text = "共 ${overview.documentCount} 张单据",
                    color = colors.muted,
                    fontSize = 12.5.sp,
                )
            }
        }

        // ---- 两段净额 ----
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                NetCard(
                    title = "卖货净额",
                    value = overview.netSaleText,
                    cents = overview.netSaleCents,
                    modifier = Modifier.weight(1f),
                )
                NetCard(
                    title = "进货净额",
                    value = overview.netPurchaseText,
                    cents = overview.netPurchaseCents,
                    modifier = Modifier.weight(1f),
                )
            }
        }

        // ---- 卖货分组 ----
        item { SectionLabel("卖货", modifier = Modifier.padding(top = 6.dp)) }
        item {
            ErpCard(padding = 0.dp) {
                Column {
                    saleTypes.forEachIndexed { index, entry ->
                        TypeRow(entry = entry, onClick = { onShowTypeOrders(entry.type) })
                        if (index != saleTypes.lastIndex) ErpHairline(Modifier.padding(horizontal = 16.dp))
                    }
                }
            }
        }

        // ---- 进货分组 ----
        item { SectionLabel("进货", modifier = Modifier.padding(top = 6.dp)) }
        item {
            ErpCard(padding = 0.dp) {
                Column {
                    purchaseTypes.forEachIndexed { index, entry ->
                        TypeRow(entry = entry, onClick = { onShowTypeOrders(entry.type) })
                        if (index != purchaseTypes.lastIndex) ErpHairline(Modifier.padding(horizontal = 16.dp))
                    }
                }
            }
        }

        // ---- 时间轴 ----
        if (overview.events.isEmpty()) {
            item { EmptyState("今天还没有开单", "开了单之后这里会按时间倒序列出。") }
        } else {
            item { SectionLabel("今天开过的单", modifier = Modifier.padding(top = 6.dp)) }
            items(overview.events, key = { "${it.orderType}-${it.sourceId}" }) { event ->
                EventRow(event = event, onClick = { onOpenDocument(event.orderType, event.sourceId) })
            }
        }
    }
}

/**
 * 净额大卡：**首屏唯一的主角**。
 *
 * 原来它和普通列表卡片长得一模一样（白底 + 同字号金额），
 * 于是「今天卖了多少」这个店主打开 App 就想知道的数，反而要在一堆同色块里找
 * （用户 2026-10-22：「颜色太单调了，分不清楚主次，看哪里都一样」）。
 *
 * 现在给它：更大的数字（26sp）+ 淡色底 + 一圈同色描边，
 * 跟下面按类型的明细行拉开一个明确的层级。
 */
@Composable
private fun NetCard(title: String, value: String, cents: Long, modifier: Modifier = Modifier) {
    val colors = LocalErpColors.current
    val shape = RoundedCornerShape(Dimens.radius)
    val negative = cents < 0
    Column(
        modifier = modifier
            .clip(shape)
            .background(if (negative) colors.danger.copy(alpha = 0.07f) else colors.primarySoft)
            // 净额卡属于**内容**不是控件，不加黑框：它靠淡蓝底 + 26sp 大数字
            // 已经有足够的存在感，再套黑框会和下面的卡片框连成一片网格。
            .border(1.dp, if (negative) colors.danger.copy(alpha = 0.22f) else colors.primary.copy(alpha = 0.18f), shape)
            .padding(horizontal = 15.dp, vertical = 15.dp),
    ) {
        Text(
            title,
            color = if (negative) colors.danger else colors.primary,
            fontSize = 11.5.sp,
            fontWeight = FontWeight.Medium,
        )
        Spacer(Modifier.height(8.dp))
        AmountText(
            text = value,
            cents = cents,
            size = 26,
            weight = FontWeight.Bold,
            tint = if (negative) colors.danger else colors.ink,
        )
    }
}

@Composable
private fun TypeRow(entry: TodayType, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(entry.label, color = colors.ink, fontSize = 14.5.sp)
        Spacer(Modifier.weight(1f))
        Text("${entry.count} 单", color = colors.muted, fontSize = 13.sp)
        Spacer(Modifier.width(14.dp))
        AmountText(text = entry.amountText, cents = entry.amountCents, size = 14.5f.toInt())
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
