package com.simpleerp.mobile.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.data.ProductDetailResponse
import com.simpleerp.mobile.data.ProductPriceRow
import com.simpleerp.mobile.ui.UiState
import com.simpleerp.mobile.ui.components.EmptyState
import com.simpleerp.mobile.ui.components.ErpCard
import com.simpleerp.mobile.ui.components.ErrorState
import com.simpleerp.mobile.ui.components.LoadingState
import com.simpleerp.mobile.ui.components.OfflineBanner
import com.simpleerp.mobile.ui.components.OrderChip
import com.simpleerp.mobile.ui.components.SectionLabel
import com.simpleerp.mobile.ui.components.SegmentedChips
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.util.formatCacheTime

/**
 * 商品详情：这个商品在**每个客户**那里的专属价。
 *
 * 差额提示（`differs` / `gapText`）由服务端算好：专属价与最近成交价不一致时
 * **不静默取其一**，而是把差额标出来 —— 这正是店主核对价格时要找的东西。
 *
 * ## 2026-10-24 改版（用户第七张截图的三条要求）
 *
 * 1. **商品名在上下滑动时固定在顶部**：标题条提到 `LazyColumn` 外面（与首页
 *    `TodayHeader` / 单据页 `OrdersFilterBar` 同一手法），滚动时不会跟着走。
 *    字号沿用首页 `.hd h1` 那一套（22sp SemiBold），看起来是同一套 App。
 * 2. 商品名下面加「搜索框 + 拼音/价格 + 正倒序箭头」，与客户页/商品页同一形态。
 * 3. **箭头点击 = 换方向 + 回顶**（`ResetScrollOnSignal` 的一次性信号）：
 *    换了顺序却停在原行号，等于让用户猜现在排的是哪一段。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ProductDetailScreen(
    state: UiState<ProductDetailResponse>,
    query: String,
    sortPinyin: Boolean,
    descending: Boolean,
    /** 列表滚动状态由上层持有（切页/返回不丢位置）。 */
    listState: LazyListState,
    onBack: () -> Unit,
    onQueryChange: (String) -> Unit,
    onToggleSort: () -> Unit,
    onToggleOrder: () -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    when (state) {
        is UiState.Loading -> LoadingState(modifier)
        is UiState.Failed -> ErrorState(state.message, modifier, onAction = onRetry)
        UiState.NeedsPairing, UiState.NeedsSetup -> LoadingState(modifier)
        is UiState.Ready -> {
            val detail = state.value
            val colors = LocalErpColors.current

            /*
             * 「商品名吸顶」用 `stickyHeader` 之外的做法：把标题条放在 LazyColumn **外面**。
             *
             * 为什么不用 `stickyHeader`：它要求内容整体在一个 LazyColumn 里，而这里
             * 标题下面还有搜索/排序两行（一起吸顶才顺手）。三行做成一个 stickyHeader
             * 会让「返回键」也跟着粘住，滚动时那三行占掉 120dp，反而更挤。
             *
             * 放在外面后，标题条天然固定，列表只吃剩余高度（`weight(1f)`），
             * 与首页 TodayScreen、单据页 OrdersScreen 的结构完全一致。
             */
            Column(modifier = modifier.fillMaxSize()) {
                ProductPinHeader(
                    title = detail.product.name,
                    onBack = onBack,
                )
                FlowRow(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = Dimens.pageGap, vertical = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    SearchBox(
                        value = query,
                        placeholder = "搜客户或首字母",
                        onValueChange = onQueryChange,
                        modifier = Modifier.weight(1f),
                    )
                    // 与客户页/商品页同构：两颗并排、选中那颗标蓝。
                    SegmentedChips(
                        options = listOf("拼音", "价格"),
                        selectedIndex = if (sortPinyin) 1 else 0,
                        onSelect = { index ->
                            if ((index == 1) != sortPinyin) onToggleSort()
                        },
                    )
                    OrderChip(descending = descending, onClick = onToggleOrder)
                }

                val prices = detail.prices
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxWidth().weight(1f),
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
                    item {
                        SectionLabel(
                            "${detail.priceCount} 个客户的价格",
                            modifier = Modifier.padding(top = 4.dp),
                        )
                    }
                    if (prices.isEmpty()) {
                        item {
                            EmptyState(
                                title = if (query.isBlank()) {
                                    "还没有客户设过这个商品的专属价"
                                } else {
                                    "没有匹配的客户"
                                },
                                hint = if (query.isBlank()) {
                                    "在电脑上开单时会自动学成专属价。"
                                } else {
                                    "换个关键字，或只输入首字母试试。"
                                },
                            )
                        }
                    }
                    items(prices, key = { it.customerId }) { price -> PriceRow(price) }

                    val missing = detail.customersWithoutPrice
                    if (missing.count > 0) {
                        item {
                            ErpCard {
                                Text(
                                    "还有 ${missing.count} 个客户没设这个商品的专属价",
                                    color = colors.muted,
                                    fontSize = 12.sp,
                                )
                                if (missing.names.isNotEmpty()) {
                                    Spacer(Modifier.height(6.dp))
                                    Text(
                                        missing.names.joinToString("、"),
                                        color = colors.muted,
                                        fontSize = 11.5.sp,
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/**
 * 吸顶标题条：返回键 + 商品名。
 *
 * 字号/字重照抄首页页头（22sp SemiBold、行高 1.25），两个页面看起来才是一套东西
 * —— 用户 2026-10-23 要求过「单据页顶部与第一页同高同字号」，这里沿用同一口径。
 *
 * 商品名可能很长（`地下式消防水泵接合器`），所以给 `maxLines = 1` + 省略号：
 * 吸顶条高度必须固定，折成两行会让下面的列表整体往下跳。
 */
@Composable
private fun ProductPinHeader(title: String, onBack: () -> Unit) {
    val colors = LocalErpColors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(colors.bg)
            .padding(start = Dimens.pageGap, end = Dimens.pageGap, top = 6.dp, bottom = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(Dimens.radiusSm))
                .clickable(onClick = onBack)
                .padding(horizontal = 6.dp, vertical = 8.dp),
        ) {
            Text("‹ 返回", color = colors.primary, fontSize = 14.5.sp)
        }
        Spacer(Modifier.width(6.dp))
        Text(
            text = title,
            color = colors.ink,
            fontSize = 22.sp,
            fontWeight = FontWeight.SemiBold,
            lineHeight = 28.sp,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
    }
}

/**
 * 搜索框：与客户页 `SearchField` 同形（白底圆角、左侧占位、右侧 ✕）。
 *
 * 单独写一份而不是复用 `CustomersScreen` 里的私有实现：那个是 `private`，
 * 抽成公共组件要动客户页（本轮不改它），所以这里保持同形即可 ——
 * 两处都只有 20 行，重复的成本比耦合低。
 */
@Composable
private fun SearchBox(
    value: String,
    placeholder: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    val shape = RoundedCornerShape(Dimens.radiusSm)
    Row(
        modifier = modifier
            .height(Dimens.minTouch)
            .clip(shape)
            .background(colors.surface)
            .border(1.dp, colors.border, shape)
            .padding(horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        BasicTextField(
            value = value,
            onValueChange = onValueChange,
            singleLine = true,
            maxLines = 1,
            textStyle = TextStyle(color = colors.ink, fontSize = 14.5.sp),
            cursorBrush = SolidColor(colors.primary),
            modifier = Modifier.weight(1f),
            decorationBox = { inner ->
                if (value.isEmpty()) {
                    Text(placeholder, color = colors.muted, fontSize = 14.5.sp)
                }
                inner()
            },
        )
        if (value.isNotEmpty()) {
            Text(
                "✕",
                color = colors.muted,
                fontSize = 14.sp,
                modifier = Modifier.clickable { onValueChange("") }.padding(start = 8.dp),
            )
        }
    }
}

@Composable
private fun PriceRow(price: ProductPriceRow) {
    val colors = LocalErpColors.current
    ErpCard(padding = 14.dp) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        price.customerName,
                        color = colors.ink,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Medium,
                        maxLines = 1,
                    )
                    if (price.customerDeleted) {
                        Spacer(Modifier.width(6.dp))
                        Text("已删除", color = colors.muted, fontSize = 10.sp)
                    }
                }
                Spacer(Modifier.height(4.dp))
                Text(
                    if (price.updatedLabel.isNotEmpty()) "更新于 ${price.updatedLabel}" else "",
                    color = colors.muted,
                    fontSize = 11.sp,
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    price.priceText,
                    color = colors.ink,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                if (price.differs && price.gapText.isNotEmpty()) {
                    Spacer(Modifier.height(4.dp))
                    // 差额用警示色：这是「该改价了」的信号，不是装饰
                    Text(
                        "上次 ${price.lastPriceText}　差 ${price.gapText}",
                        color = colors.warn,
                        fontSize = 10.5.sp,
                        modifier = Modifier
                            .clip(RoundedCornerShape(5.dp))
                            .background(colors.warnBg)
                            .padding(horizontal = 5.dp, vertical = 2.dp),
                    )
                }
            }
        }
    }
}
