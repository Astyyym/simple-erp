package com.simpleerp.mobile.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import com.simpleerp.mobile.ui.components.SegmentedChips
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
@Composable
fun ProductsScreen(
    state: UiState<ProductsResponse>,
    query: String,
    sortPrice: Boolean,
    descending: Boolean,
    onQueryChange: (String) -> Unit,
    onToggleSort: () -> Unit,
    onToggleOrder: () -> Unit,
    onOpenProduct: (Int) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    // 排序变了要回到顶部（用户 2026-10-22 要求）。
    // 不回到顶部的话，用户滚到第 30 行再切排序，看到的是新排序的第 30 行 ——
    // 完全不知道自己排的是哪一段，看起来像「排序没生效」。
    val listState = rememberLazyListState()
    LaunchedEffect(sortPrice, descending) {
        listState.scrollToItem(0)
    }
    Column(modifier = modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = Dimens.pageGap, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
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
            Spacer(Modifier.width(8.dp))
            // 与客户页同构：两颗并排、选中那颗标蓝。
            SegmentedChips(
                options = listOf("拼音", "价格"),
                selectedIndex = if (sortPrice) 1 else 0,
                onSelect = { index ->
                    if ((index == 1) != sortPrice) onToggleSort()
                },
            )
            Spacer(Modifier.width(6.dp))
            OrderChip(descending = descending, onClick = onToggleOrder)
        }

        when (state) {
            is UiState.Loading -> LoadingState()
            is UiState.Failed -> ErrorState(state.message, onAction = onRetry)
            UiState.NeedsPairing, UiState.NeedsSetup -> LoadingState()
            is UiState.Ready -> {
                val products = state.value.products
                LazyColumn(
                    state = listState,
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
                        // **一整张卡里分行，不要每行一张卡**（2026-10-22 对齐网页版定稿）。
                        // 原来每行一张白卡：一样高、一样白、一样圆角，几十张排下去是一堵
                        // 方块墙，眼睛找不到落点，用户直接说「丑」。网页版 customers/orders/
                        // products 三个列表页一直是「一张 card + 内部 row + 发丝线分隔」，
                        // 原生端从没照做 —— 这次补上。
                        item {
                            ErpCard(padding = 0.dp) {
                                Column {
                                    products.forEachIndexed { index, product ->
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
