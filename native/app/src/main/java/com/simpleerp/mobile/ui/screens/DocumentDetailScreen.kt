package com.simpleerp.mobile.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.data.DocumentDetail
import com.simpleerp.mobile.data.DocumentItem
import com.simpleerp.mobile.ui.UiState
import com.simpleerp.mobile.ui.components.AmountText
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
 * 单据详情：明细行 + 合计。
 *
 * 详情页用**精确金额**（`money_exact`，带两位小数），列表页用 `money_short`。
 * 两个字符串都由服务端给，客户端不做任何格式化 —— 这是「口径只有一份」的落点。
 */
@Composable
fun DocumentDetailScreen(
    state: UiState<DocumentDetail>,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    when (state) {
        is UiState.Loading -> LoadingState(modifier)
        is UiState.Failed -> ErrorState(state.message, modifier, onAction = onRetry)
        UiState.NeedsPairing, UiState.NeedsSetup -> LoadingState(modifier)
        is UiState.Ready -> {
            val doc = state.value
            val colors = LocalErpColors.current
            LazyColumn(
                modifier = modifier.fillMaxWidth(),
                contentPadding = PaddingValues(
                    start = Dimens.pageGap,
                    end = Dimens.pageGap,
                    top = 8.dp,
                    bottom = 28.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                item { BackBar(title = doc.typeLabel, onBack = onBack) }
                if (state.fromCache) {
                    item { OfflineBanner(formatCacheTime(state.fetchedAt)) }
                }

                // ---- 单据抬头 ----
                // 用户 2026-10-22 报「没有层次，颜色样式字体大小也没有主次之分」。
                // 原来客户名 18sp 和下面的明细行 14sp 差距太小，加上整页全是白卡，
                // 一眼看过去是一片均匀的方块。现在按**三级**重排：
                //   1. 金额（27sp Bold，全页最大的数）
                //   2. 客户名（21sp SemiBold）
                //   3. 单号/日期（12.5sp muted）
                item {
                    // 抬头卡铺一层极淡的类型色底：销售=蓝、退货=红、拿货=琥珀。
                    // 纯白卡跟下面的明细卡同色，「这是一张什么单」得靠读文字才知道；
                    // 上色之后余光一扫就能分出单据类型，也把抬头和明细在视觉上分开。
                    val headShape = androidx.compose.foundation.shape.RoundedCornerShape(Dimens.radius)
                    val headTint = when (doc.orderType) {
                        "sale" -> colors.primary
                        "return" -> colors.danger
                        "purchase" -> colors.warn
                        else -> colors.muted
                    }
                    ErpCard(padding = 0.dp) {
                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clip(headShape)
                                .background(headTint.copy(alpha = 0.06f))
                                .padding(17.dp),
                        ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            TypeBadge(doc.typeLabel, doc.orderType)
                            Spacer(Modifier.width(8.dp))
                            Text(
                                doc.statusLabel,
                                color = colors.muted,
                                fontSize = 11.5.sp,
                            )
                        }
                        Spacer(Modifier.height(11.dp))
                        Text(
                            doc.customerName,
                            color = colors.ink,
                            fontSize = 21.sp,
                            fontWeight = FontWeight.SemiBold,
                            maxLines = 2,
                        )
                        Spacer(Modifier.height(7.dp))
                        Text(
                            "${doc.dateLabel}　${doc.orderNo}",
                            color = colors.muted,
                            fontSize = 12.sp,
                        )
                        Spacer(Modifier.height(13.dp))
                        ErpHairline()
                        Spacer(Modifier.height(13.dp))
                        // 金额是这张单唯一的结论，单独占一行、给最大字号。
                        Text("单据金额", color = colors.muted, fontSize = 11.5.sp)
                        Spacer(Modifier.height(5.dp))
                        AmountText(
                            text = doc.totalText,
                            cents = doc.totalCents,
                            size = 27,
                            weight = FontWeight.Bold,
                        )
                        }
                    }
                }

                // 明细原来是「一项一张卡」，5 项就是 5 张白卡，视觉上跟抬头卡同权，
                // 整页全是等大的方块 → 没有层次。改成**一张卡内分行**，行间用发丝线分隔，
                // 这样「明细」是一个整体块，跟抬头、合计形成三段结构。
                if (doc.items.isEmpty()) {
                    // 离线降级页：单据从没联网打开过，只有列表里的摘要。
                    // 这里必须**说清楚**为什么没有明细，否则用户会以为这单真的没商品。
                    item { SectionLabel("明细", modifier = Modifier.padding(top = 4.dp)) }
                    item {
                        ErpCard {
                            Text(
                                "明细需要联网才能看。上面这些是从列表里带过来的，"
                                    + "单号、日期、金额都是准的。",
                                color = colors.muted,
                                fontSize = 12.5.sp,
                            )
                        }
                    }
                } else {
                    item { SectionLabel("明细（${doc.items.size} 项）", modifier = Modifier.padding(top = 4.dp)) }
                    item {
                        ErpCard(padding = 0.dp) {
                            Column {
                                doc.items.forEachIndexed { index, line ->
                                    ItemRow(line, index + 1)
                                    if (index != doc.items.lastIndex) {
                                        ErpHairline(Modifier.padding(horizontal = 16.dp))
                                    }
                                }
                            }
                        }
                    }
                }

                // 合计：全页的结论，给淡蓝底 + 最大字号，跟明细块明确分开。
                item {
                    val sumShape = androidx.compose.foundation.shape.RoundedCornerShape(Dimens.radius)
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(sumShape)
                            .background(colors.primarySoft)
                            .border(1.dp, colors.primary.copy(alpha = 0.18f), sumShape)
                            .padding(horizontal = 17.dp, vertical = 14.dp),
                    ) {
                        Text(
                            "本单合计",
                            color = colors.primary,
                            fontSize = 11.5.sp,
                            fontWeight = FontWeight.Medium,
                        )
                        Spacer(Modifier.height(5.dp))
                        AmountText(
                            text = doc.totalText,
                            cents = doc.totalCents,
                            size = 25,
                            weight = FontWeight.Bold,
                        )
                    }
                }

                if (doc.notes.isNotEmpty()) {
                    item {
                        ErpCard {
                            Text("备注", color = colors.muted, fontSize = 11.5.sp)
                            Spacer(Modifier.height(6.dp))
                            Text(doc.notes, color = colors.ink, fontSize = 13.5.sp)
                        }
                    }
                }
            }
        }
    }
}

/**
 * 明细行（**不自己包卡**，由外层一张卡内分行）。
 *
 * 行内三级：品名 15sp Medium（主体）/ 小计 15.5sp SemiBold（金额）/
 * 「数量 × 单价」11.5sp muted（推算过程，可核对但不需要抢眼）。
 */
@Composable
private fun ItemRow(item: DocumentItem, index: Int) {
    val colors = LocalErpColors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // 序号徽章：明细行之间只有发丝线分隔，长单子容易看串行。
        // 给每行一个 20dp 的圆角数字，眼睛有锚点，也顺手能数「第几项」。
        Box(
            modifier = Modifier
                .size(20.dp)
                .clip(RoundedCornerShape(6.dp))
                .background(colors.soft),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = index.toString(),
                color = colors.muted,
                fontSize = 10.5.sp,
                fontWeight = FontWeight.Medium,
            )
        }
        Spacer(Modifier.width(10.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                item.name,
                color = colors.ink,
                fontSize = 15.sp,
                fontWeight = FontWeight.Medium,
                maxLines = 2,
            )
            Spacer(Modifier.height(5.dp))
            Text(
                "${item.quantityText} × ${item.unitPriceText}",
                color = colors.muted,
                fontSize = 11.5.sp,
            )
        }
        Spacer(Modifier.width(10.dp))
        AmountText(item.subtotalText, item.subtotalCents, size = 16, weight = FontWeight.SemiBold)
    }
}
