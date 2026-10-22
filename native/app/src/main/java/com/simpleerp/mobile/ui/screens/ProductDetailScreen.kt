package com.simpleerp.mobile.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
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
import com.simpleerp.mobile.ui.components.SectionLabel
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.util.formatCacheTime

/**
 * 商品详情：这个商品在**每个客户**那里的专属价。
 *
 * 差额提示（`differs` / `gapText`）由服务端算好：专属价与最近成交价不一致时
 * **不静默取其一**，而是把差额标出来 —— 这正是店主核对价格时要找的东西。
 */
@Composable
fun ProductDetailScreen(
    state: UiState<ProductDetailResponse>,
    onBack: () -> Unit,
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
            LazyColumn(
                modifier = modifier.fillMaxWidth(),
                contentPadding = PaddingValues(
                    start = Dimens.pageGap,
                    end = Dimens.pageGap,
                    top = 8.dp,
                    bottom = 28.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                item { BackBar(title = detail.product.name, onBack = onBack) }
                if (state.fromCache) {
                    item { OfflineBanner(formatCacheTime(state.fetchedAt)) }
                }
                item {
                    SectionLabel("${detail.priceCount} 个客户的价格", modifier = Modifier.padding(top = 4.dp))
                }
                if (detail.prices.isEmpty()) {
                    item {
                        EmptyState(
                            title = "还没有客户设过这个商品的专属价",
                            hint = "在电脑上开单时会自动学成专属价。",
                        )
                    }
                }
                items(detail.prices, key = { it.customerId }) { price -> PriceRow(price) }

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
