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
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.data.ProductSummary
import com.simpleerp.mobile.data.ProductsResponse
import com.simpleerp.mobile.ui.UiState
import com.simpleerp.mobile.ui.components.EmptyState
import com.simpleerp.mobile.ui.components.ErpCard
import com.simpleerp.mobile.ui.components.ErpHairline
import com.simpleerp.mobile.ui.components.ErrorState
import com.simpleerp.mobile.ui.components.LoadingState
import com.simpleerp.mobile.ui.components.OfflineBanner
import com.simpleerp.mobile.ui.components.OrderChip
import com.simpleerp.mobile.ui.components.Refreshable
import com.simpleerp.mobile.ui.components.SegmentedChips
import com.simpleerp.mobile.ui.components.listRowBorder
import com.simpleerp.mobile.ui.components.listRowShape
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.util.formatCacheTime

/**
 * 商品价：**商品优先**（用户 2026-10-17 晚二次修订的最终形态）。
 *
 * 不再先选客户：直接搜商品名/拼音，卡片显示「N 个客户 + 价格区间」，
 * 点进去看这个商品在每个客户处的具体价格。
 * 理由：店主核对价格是双向的，只给客户优先就把另一半藏起来了。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ProductsScreen(
    state: UiState<ProductsResponse>,
    query: String,
    sortPrice: Boolean,
    descending: Boolean,
    /** 列表滚动状态由上层持有（切 tab / 返回详情不丢位置，2026-10-22 修）。 */
    listState: LazyListState,
    refreshing: Boolean,
    onRefresh: () -> Unit,
    onQueryChange: (String) -> Unit,
    onToggleSort: () -> Unit,
    onToggleOrder: () -> Unit,
    onOpenProduct: (Int) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    // 不在 Composable 里自动回顶：`LaunchedEffect` 重新进入组合时也会跑，
    // 切 tab 回来就会把位置清掉（2026-10-22 实测）。换排序回顶改在 AppRoot 的
    // 排序回调里做（只在用户点击时触发）。
    Column(modifier = modifier.fillMaxWidth()) {
        // FlowRow：窄屏放不下「搜索框 + 排序 chips + 方向箭头」时自动换行（2026-10-22 修）。
        FlowRow(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = Dimens.pageGap, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            val shape = RoundedCornerShape(Dimens.radiusSm)
            Row(
                modifier = Modifier
                    .weight(1f)
                    .height(Dimens.minTouch)
                    .clip(shape)
                    .background(colors.surface)
                    .padding(horizontal = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                BasicTextField(
                    value = query,
                    onValueChange = onQueryChange,
                    singleLine = true,
                    maxLines = 1,
                    textStyle = TextStyle(color = colors.ink, fontSize = 14.5.sp),
                    cursorBrush = SolidColor(colors.primary),
                    modifier = Modifier.weight(1f),
                    decorationBox = { inner ->
                        if (query.isEmpty()) {
                            // 短占位符：右边还要放排序 chips + 方向箭头，
                            // 原来 10 个字在窄屏会把搜索框撑成两行。
                            Text("搜商品或首字母", color = colors.muted, fontSize = 14.5.sp)
                        }
                        inner()
                    },
                )
                if (query.isNotEmpty()) {
                    Text(
                        "✕",
                        color = colors.muted,
                        fontSize = 14.sp,
                        modifier = Modifier.clickable { onQueryChange("") }.padding(start = 8.dp),
                    )
                }
            }
            // 与客户页同构：两颗并排、选中那颗标蓝。
            SegmentedChips(
                options = listOf("拼音", "价格"),
                selectedIndex = if (sortPrice) 1 else 0,
                onSelect = { index ->
                    if ((index == 1) != sortPrice) onToggleSort()
                },
            )
            OrderChip(descending = descending, onClick = onToggleOrder)
        }

        when (state) {
            is UiState.Loading -> LoadingState()
            is UiState.Failed -> ErrorState(state.message, onAction = onRetry)
            UiState.NeedsPairing, UiState.NeedsSetup -> LoadingState()
            is UiState.Ready -> {
                val products = state.value.products
                Refreshable(refreshing = refreshing, onRefresh = onRefresh) {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(
                            start = Dimens.pageGap,
                            end = Dimens.pageGap,
                            bottom = 28.dp,
                        ),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        if (state.fromCache) {
                            item { OfflineBanner(formatCacheTime(state.fetchedAt)) }
                        }
                        if (products.isEmpty()) {
                            item {
                                EmptyState(
                                    title = if (query.isBlank()) "还没有商品" else "没找到这个商品",
                                    hint = if (query.isBlank()) "" else "换个关键词，或只输入首字母试试。",
                                )
                            }
                        } else {
                            // **每行一个 item**（2026-10-22 修 N-2）：一整个 Column 塞单个 item
                            // 时，切 tab 重新测量会把滚动偏移夹到 0。视觉仍是一张整卡。
                            products.forEachIndexed { index, product ->
                                item(key = product.id) {
                                    ErpCard(
                                        padding = 0.dp,
                                        elevated = index == 0,
                                        bordered = false,
                                        shape = listRowShape(
                                            isFirst = index == 0,
                                            isLast = index == products.lastIndex,
                                        ),
                                        modifier = Modifier.listRowBorder(
                                            colors = colors,
                                            isFirst = index == 0,
                                            isLast = index == products.lastIndex,
                                        ),
                                    ) {
                                        ProductRow(
                                            product = product,
                                            onClick = { onOpenProduct(product.id) },
                                        )
                                        if (index != products.lastIndex) {
                                            ErpHairline(Modifier.padding(horizontal = 16.dp))
                                        }
                                    }
                                }
                            }
                        }
                        // 商品列表有 60 条上限（服务端 PRODUCT_LIST_LIMIT）。
                        // 原来完全没提示，超过 60 个时后面的商品「搜不到也看不到」，
                        // 店主会以为「就这么多」（2026-10-22 补，对齐单据页的提示）。
                        if (state.value.truncated) {
                            item {
                                Text(
                                    "只显示了前 60 个商品，输入名称或拼音缩小范围可以看到更多。",
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
 * 一行商品。**严格照网页版 2026-10-18 定稿（A 方案 + 蓝色药丸）重做**：
 *
 * 1. 卡片压成**两行**：商品名一行、价格行一行。原来三行等宽，一屏只能看 4 个。
 * 2. 价格做成**蓝色药丸**（primary 字 + primarySoft 底 + 7dp 圆角）：
 *    柜台前第一眼看的就是钱，给它最高视觉权重。原来只是把数字染蓝，
 *    跟商品名平级，抓不住眼。
 * 3. **去掉重复标签和单位**：每张卡都写一遍「N 个客户有专属价」是纯占地方；
 *    单位用户明确不需要。药丸因此成了行里唯一的胶囊，不打架。
 */
@Composable
private fun ProductRow(product: ProductSummary, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 13.dp),
    ) {
        Text(
            product.name,
            color = colors.ink,
            fontSize = 14.5.sp,
            fontWeight = FontWeight.SemiBold,
            lineHeight = 20.sp,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (product.minPriceText != null) {
                Text(
                    text = if (product.minPriceText == product.maxPriceText) {
                        product.minPriceText
                    } else {
                        "${product.minPriceText} – ${product.maxPriceText}"
                    },
                    color = colors.primary,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier
                        .clip(RoundedCornerShape(7.dp))
                        .background(colors.primarySoft)
                        .padding(horizontal = 9.dp, vertical = 3.dp),
                )
                Spacer(Modifier.width(10.dp))
            }
            Text(
                text = if (product.priceCount > 0) {
                    "${product.priceCount} 个客户"
                } else {
                    "还没有客户专属价"
                },
                color = colors.muted,
                fontSize = 11.5.sp,
            )
        }
    }
}
