package com.simpleerp.mobile.ui.screens

import androidx.compose.foundation.background
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.data.CustomerDetailResponse
import com.simpleerp.mobile.data.CustomerDetail
import com.simpleerp.mobile.data.CustomerPrice
import com.simpleerp.mobile.data.DocumentSummary
import com.simpleerp.mobile.data.LedgerEntry
import com.simpleerp.mobile.ui.UiState
import com.simpleerp.mobile.ui.components.AmountText
import com.simpleerp.mobile.ui.components.orderTypeColor
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
 * 客户详情「半详情」（用户 2026-10-17 定案做）：余额三段 + 最近流水 + 最近单据 + 专属价。
 *
 * 不做日期筛选、不做全量分页 —— 那不是这个页面要回答的问题。
 * 余额只来自 `get_customer_account_ledger()`（服务端），客户端不重算。
 */
@Composable
fun CustomerDetailScreen(
    state: UiState<CustomerDetailResponse>,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onOpenDocument: (orderType: String, id: Int) -> Unit,
    onShowAllOrders: (customerId: Int, customerName: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    when (state) {
        is UiState.Loading -> LoadingState(modifier)
        is UiState.Failed -> ErrorState(state.message, modifier, onAction = onRetry)
        UiState.NeedsPairing, UiState.NeedsSetup -> LoadingState(modifier)
        is UiState.Ready -> {
            val data = state.value
            val colors = LocalErpColors.current
            // 三段各自独立折叠（用户 2026-10-22 要求）：默认各显示 3 条，
            // 点「展开」就在**当前页原地铺开**，不跳页、不重新请求、不回到顶部。
            // 用三个独立布尔而不是一个总开关：用户可能只想展开流水，不想把
            // 专属价那几十行一起铺开（专属价常有几十条，全展开会把流水顶到很下面）。
            var expandDocs by rememberSaveable { mutableStateOf(false) }
            var expandPrices by rememberSaveable { mutableStateOf(false) }
            var expandEntries by rememberSaveable { mutableStateOf(false) }
            val shownDocs = if (expandDocs) data.documents else data.documents.take(COLLAPSED_ROWS)
            val shownPrices = if (expandPrices) data.prices else data.prices.take(COLLAPSED_ROWS)
            val shownEntries = if (expandEntries) data.entries else data.entries.take(COLLAPSED_ROWS)
            // 顶部两段**固定不动**（用户 2026-10-22 要求）：
            // - 返回栏：往下翻到很远也能一眼回去，不用先滑回顶部；
            // - 余额行：这是客户页最重要的一个数，翻单据/流水时随时要对照，
            //   滚走了就得反复上下滑。
            //
            // 结构上从「整页一个 LazyColumn」改成「Column(固定头 + 可滚内容)」。
            // 为什么不用 LazyColumn 的 `stickyHeader`：stickyHeader 只吸顶到
            // 内容区顶部，而我们要的是「两个块都不参与滚动」，语义不一样；
            // 而且 stickyHeader 在快速滑动时会有贴图抖动。
            Column(modifier = modifier.fillMaxWidth()) {
                BackBar(
                    title = data.customer.name,
                    onBack = onBack,
                    modifier = Modifier.padding(horizontal = Dimens.pageGap),
                )
                BalanceStrip(
                    customer = data.customer,
                    modifier = Modifier.padding(
                        start = Dimens.pageGap,
                        end = Dimens.pageGap,
                        top = 6.dp,
                        bottom = 8.dp,
                    ),
                )

                LazyColumn(
                    modifier = Modifier.fillMaxWidth(),
                    contentPadding = PaddingValues(
                        start = Dimens.pageGap,
                        end = Dimens.pageGap,
                        top = 4.dp,
                        bottom = 28.dp,
                    ),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                if (state.fromCache) {
                    item { OfflineBanner(formatCacheTime(state.fetchedAt)) }
                }

                // ---- 联系方式 ----
                // 余额搬到顶部固定区之后，这张卡只剩电话/地址。
                // 它们是低频信息，用最小的一张卡收着，不再和余额抢版面。
                if (data.customer.phone.isNotEmpty() || data.customer.address.isNotEmpty()) {
                    item {
                        ErpCard {
                            if (data.customer.phone.isNotEmpty()) {
                                InfoLine("电话", data.customer.phone)
                            }
                            if (data.customer.address.isNotEmpty()) {
                                InfoLine("地址", data.customer.address)
                            }
                        }
                    }
                }

                // ---- 最近单据 ----
                //
                // 段头**没有**「看全部」了（用户 2026-10-22 指出功能重复）：
                // 段末已经有「展开全部 N 条」，两个都是「看更多单据」，
                // 区别只是「在本页展开」还是「跳页筛选」，用户看不出这个区别，
                // 只会觉得两个按钮干同一件事。保留展开（不跳页、不丢上下文）。
                item {
                    SectionLabel("最近单据", modifier = Modifier.padding(top = 4.dp))
                }
                if (data.documents.isEmpty()) {
                    item { EmptyState("还没有单据") }
                } else {
                    item {
                        ErpCard(padding = 0.dp) {
                            Column {
                                shownDocs.forEachIndexed { index, doc ->
                                    DocumentRow(
                                        doc = doc,
                                        onClick = { onOpenDocument(doc.orderType, doc.id) },
                                    )
                                    if (index != shownDocs.lastIndex) {
                                        ErpHairline(Modifier.padding(horizontal = 16.dp))
                                    }
                                }
                            }
                        }
                    }
                    // 「展开」放在**与「看全部」同一行同一侧**的位置（用户 2026-10-22 指定）：
                    // 都在 SectionLabel 的 trailing 里，视觉上是一条右对齐的操作位。
                    // 区别是「看全部」跳页筛单据，「展开」只在本页铺开剩余几条。
                    if (data.documents.size > COLLAPSED_ROWS) {
                        item {
                            ExpandRow(
                                expanded = expandDocs,
                                total = data.documents.size,
                                onToggle = { expandDocs = !expandDocs },
                            )
                        }
                    }
                }

                // ---- 专属价 ----
                if (data.prices.isNotEmpty()) {
                    item {
                        SectionLabel(
                            "专属价（${data.priceCount}）",
                            modifier = Modifier.padding(top = 6.dp),
                        )
                    }
                    item {
                        ErpCard(padding = 0.dp) {
                            Column {
                                shownPrices.forEachIndexed { index, price ->
                                    PriceRow(price)
                                    if (index != shownPrices.lastIndex) {
                                        ErpHairline(Modifier.padding(horizontal = 16.dp))
                                    }
                                }
                            }
                        }
                    }
                    if (data.prices.size > COLLAPSED_ROWS) {
                        item {
                            ExpandRow(
                                expanded = expandPrices,
                                total = data.prices.size,
                                onToggle = { expandPrices = !expandPrices },
                            )
                        }
                    }
                }

                // ---- 流水 ----
                item {
                    SectionLabel("最近流水", modifier = Modifier.padding(top = 6.dp))
                }
                if (data.entries.isEmpty()) {
                    item { EmptyState("还没有往来记录") }
                } else {
                    items(shownEntries) { entry -> LedgerRow(entry) }
                    if (data.entries.size > COLLAPSED_ROWS) {
                        item {
                            ExpandRow(
                                expanded = expandEntries,
                                total = data.entries.size,
                                onToggle = { expandEntries = !expandEntries },
                            )
                        }
                    }
                }
                }   // LazyColumn
                }   // Column（固定头 + 可滚内容）
            }   // is UiState.Ready
    }   // when
}

/** 折叠时每段显示几条（用户 2026-10-22 定：默认三条）。 */
private const val COLLAPSED_ROWS = 3

/**
 * 顶部固定的余额条：**一行**，标签在左、金额在右。
 *
 * 用户 2026-10-22 要求：「当前欠款/当前多付 和对应的数字，变成一行，也做成顶部固定」。
 *
 * 原来是一整块大卡（标签一行、32sp 金额一行、期初/本期变动一行），
 * 垂直方向吃掉近 120dp。固定到顶部之后这个高度不能接受 —— 屏幕就那么高，
 * 顶部占太多，下面的单据/流水能看的内容就少了。压成一行之后约 56dp，
 * 既保住「最重要的数是常驻的」，又不挤压内容区。
 *
 * 期初/本期变动没有丢：它们挪到金额下面一行小字（still 在固定区内），
 * 因为店主对账时确实要看到「这个余额是怎么来的」。
 */
@Composable
private fun BalanceStrip(customer: CustomerDetail, modifier: Modifier = Modifier) {
    val colors = LocalErpColors.current
    val owed = customer.currentBalanceCents > 0
    val prepaid = customer.currentBalanceCents < 0
    // 颜色语义（用户 2026-10-22 明确指定）：
    // - 欠款 = 别人欠我的 = **绿色**（钱要进来）；
    // - 多付 = 我欠别人的 = **红色**（钱要出去）。
    // 跟金额正负的直觉相反（正数是欠款），所以不能沿用 AmountText 默认配色。
    val tint = when {
        owed -> colors.success
        prepaid -> colors.danger
        else -> colors.ink
    }
    val label = when {
        owed -> "当前欠款"
        prepaid -> "当前多付"
        else -> "当前余额"
    }

    ErpCard(padding = 0.dp, modifier = modifier) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // 圆角小标签：一眼能认出「这是状态」，而不是一个普通说明词。
            Text(
                text = label,
                color = tint,
                fontSize = 11.5.sp,
                fontWeight = FontWeight.Medium,
                modifier = Modifier
                    .clip(RoundedCornerShape(7.dp))
                    .background(tint.copy(alpha = 0.14f))
                    .padding(horizontal = 9.dp, vertical = 4.dp),
            )
            Spacer(Modifier.weight(1f))
            Column(horizontalAlignment = Alignment.End) {
                AmountText(
                    text = customer.currentBalanceText,
                    cents = customer.currentBalanceCents,
                    size = 22,
                    weight = FontWeight.Bold,
                    tint = tint,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    text = "期初 ${formatCentsText(customer.openingBalanceCents)} · 本期 ${customer.changeText}",
                    color = colors.muted,
                    fontSize = 10.5.sp,
                    maxLines = 1,
                )
            }
        }
    }
}

/**
 * 「展开 / 收起」行。
 *
 * 放在段末而不是段头：段头右侧已经有「看全部」（跳页筛单据），
 * 两个操作挤同一行会让人分不清「哪个是跳页、哪个是就地展开」。
 * 段末的展开行明确写着「还有 N 条」，语义没有歧义。
 *
 * 只改本地布尔，**不触发任何网络请求**，所以不会回顶、不会闪屏 ——
 * 用户 2026-10-22 明确要求「在当前页面展开，不要回顶刷新」。
 */
@Composable
private fun ExpandRow(expanded: Boolean, total: Int, onToggle: () -> Unit) {
    val colors = LocalErpColors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(Dimens.radiusSm))
            .clickable(onClick = onToggle)
            .padding(vertical = 11.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = if (expanded) "收起" else "展开全部 $total 条",
            color = colors.primary,
            fontSize = 12.5.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
fun BackBar(
    title: String,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
    trailing: @Composable (() -> Unit)? = null,
) {
    val colors = LocalErpColors.current
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(Dimens.radiusSm))
                .clickable(onClick = onBack)
                .padding(horizontal = 6.dp, vertical = 10.dp),
        ) {
            Text("‹ 返回", color = colors.primary, fontSize = 14.5.sp)
        }
        Spacer(Modifier.width(8.dp))
        Text(
            text = title,
            color = colors.ink,
            fontSize = 16.5.sp,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1,
            modifier = Modifier.weight(1f),
        )
        if (trailing != null) trailing()
    }
}

@Composable
private fun MiniStat(label: String, value: String) {
    val colors = LocalErpColors.current
    Column {
        Text(label, color = colors.muted, fontSize = 11.sp)
        Spacer(Modifier.height(3.dp))
        Text(value, color = colors.ink, fontSize = 14.sp, fontWeight = FontWeight.Medium)
    }
}

/**
 * 联系方式一行。
 *
 * 标签从「裸文字」改成**小方块标签**：电话/地址是查得到的辅助信息，
 * 用 36dp 固定宽度的灰底小标签把它和值分开，扫读时不会把标签看成内容的一部分。
 */
@Composable
private fun InfoLine(label: String, value: String) {
    val colors = LocalErpColors.current
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(5.dp))
                .background(colors.soft)
                .padding(horizontal = 6.dp, vertical = 3.dp),
        ) {
            Text(label, color = colors.muted, fontSize = 10.5.sp, fontWeight = FontWeight.Medium)
        }
        Spacer(Modifier.width(10.dp))
        Text(
            value,
            color = colors.ink,
            fontSize = 13.5.sp,
            modifier = Modifier.weight(1f),
        )
    }
}

/**
 * 单据行。**不自己包卡** —— 由调用方（单据列表 / 客户详情）放进一张卡内分行，
 * 行间用 ErpHairline 分隔。这是网页版 `orders/list.html` 的定稿结构：
 * 原来每行一张白卡，几十上百张等大方块排下去是一堵墙，没有视觉落点。
 */
@Composable
fun DocumentRow(doc: DocumentSummary, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                TypeBadge(doc.typeLabel, doc.orderType)
                Spacer(Modifier.width(8.dp))
                Text(
                    doc.customerName,
                    color = colors.ink,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.height(4.dp))
            Text(
                "${doc.dateLabel}　${doc.orderNo}",
                color = colors.muted,
                fontSize = 11.5.sp,
                maxLines = 1,
            )
        }
        Spacer(Modifier.width(10.dp))
        // 金额颜色跟类型标签一致（用户 2026-10-22 要求）。
        // 原来金额是「负数红、正数黑」，但退货单金额是正的也会显示黑色，
        // 扫一眼分不清这张单是进账还是出账。用类型色之后，
        // 蓝色=销售、红色=退货、橙色=拿货，金额和标签同色，一眼成对。
        AmountText(
            doc.amountText,
            doc.amountCents,
            size = 15,
            weight = FontWeight.SemiBold,
            tint = orderTypeColor(doc.orderType),
        )
    }
}

@Composable
private fun PriceRow(price: CustomerPrice) {
    val colors = LocalErpColors.current
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(price.productName, color = colors.ink, fontSize = 14.sp, maxLines = 1)
            if (price.updatedLabel.isNotEmpty()) {
                Spacer(Modifier.height(3.dp))
                Text(price.updatedLabel, color = colors.muted, fontSize = 11.sp)
            }
        }
        Spacer(Modifier.width(10.dp))
        Text(
            price.priceText,
            color = colors.ink,
            fontSize = 14.5.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun LedgerRow(entry: LedgerEntry) {
    val colors = LocalErpColors.current
    ErpCard(padding = 14.dp) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    entry.typeLabel.ifEmpty { entry.type },
                    color = colors.ink,
                    fontSize = 13.5.sp,
                    fontWeight = FontWeight.Medium,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    listOf(entry.dateLabel, entry.description)
                        .filter { it.isNotBlank() }
                        .joinToString("　"),
                    color = colors.muted,
                    fontSize = 11.5.sp,
                    maxLines = 2,
                )
            }
            Spacer(Modifier.width(10.dp))
            AmountText(entry.amountText, entry.amountCents, size = 14)
        }
    }
}

/**
 * 只在**没有服务端格式化文本**时兜底（期初余额目前没有 text 字段）。
 * 有 `*_text` 的地方一律用它，不要走这里。
 */
private fun formatCentsText(cents: Long): String {
    val sign = if (cents < 0) "-" else ""
    val abs = kotlin.math.abs(cents)
    val yuan = abs / 100
    val fen = abs % 100
    return if (fen == 0L) "$sign¥$yuan" else "$sign¥$yuan.${fen.toString().padStart(2, '0')}"
}
