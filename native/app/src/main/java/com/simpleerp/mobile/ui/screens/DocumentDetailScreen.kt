package com.simpleerp.mobile.ui.screens

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
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
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.lerp
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
import com.simpleerp.mobile.ui.components.orderTypeColor
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.util.formatCacheTime

/**
 * 单据详情：抬头（可折叠）+ 明细行。
 *
 * 详情页用**精确金额**（`money_exact`，带两位小数），列表页用 `money_short`。
 * 两个字符串都由服务端给，客户端不做任何格式化 —— 这是「口径只有一份」的落点。
 *
 * ## 2026-10-23 改版（用户要求）
 *
 * 1. **抬头压成一行**：`客户名  金额`，金额紧跟在客户名右侧（原来金额单独一大块在
 *    最下面，跟客户名隔着日期、分隔线，眼睛要来回跳）。金额颜色改成**与类型标签同色**
 *    （`orderTypeColor`，销售蓝/退货红/拿货橙），扫一眼就知道这是进账还是出账。
 * 2. **删掉「本单合计」块**：金额已经上提到抬头，再在页尾重复一遍就是两块同样的数。
 *    明细行的金额加起来就是结论，不需要第二张卡再说一次。
 * 3. **抬头吸顶 + 折叠动画**：明细多的时候，抬头固定在顶部、随滚动压成一行
 *    `客户名  金额`（字号 21sp → 15sp，27sp → 15sp），回顶再展开。
 *    手法与首页 `TodayHeader` 完全一致：高度 lerp + 双层交叉淡入 + 180ms tween，
 *    不用 `AnimatedContent`（换布局会在阈值来回时跳）。
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
            val listState = rememberLazyListState()

            /*
             * 折叠进度：0 = 回顶展开，1 = 压成一行。
             *
             * 抬头放在列表**外面**，用 listState 的偏移直接驱动 —— 与首页同一套做法：
             * 不用 stickyHeader（那会在快速滑动时抖动），也不依赖 nestedScroll。
             */
            val progress by remember {
                derivedStateOf {
                    if (listState.firstVisibleItemIndex > 0) {
                        1f
                    } else {
                        (listState.firstVisibleItemScrollOffset / DocHeaderCollapsePx).coerceIn(0f, 1f)
                    }
                }
            }
            val animated by animateFloatAsState(
                targetValue = progress,
                animationSpec = tween(durationMillis = 180),
                label = "docHeaderCollapse",
            )

            Column(modifier = modifier.fillMaxSize()) {
                // 返回栏常驻：翻到很远也能一眼回去，不用先滑回顶部。
                BackBar(
                    title = doc.typeLabel,
                    onBack = onBack,
                    modifier = Modifier.padding(horizontal = Dimens.pageGap),
                )
                DocumentHeader(doc = doc, progress = animated)

                if (state.fromCache) {
                    OfflineBanner(
                        formatCacheTime(state.fetchedAt),
                        modifier = Modifier.padding(horizontal = Dimens.pageGap, vertical = 6.dp),
                    )
                }

                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(
                        start = Dimens.pageGap,
                        end = Dimens.pageGap,
                        top = 4.dp,
                        bottom = 28.dp,
                    ),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    if (doc.items.isEmpty()) {
                        // 离线降级页：单据从没联网打开过，只有列表里的摘要。
                        // 这里必须**说清楚**为什么没有明细，否则用户会以为这单真的没商品。
                        item { SectionLabel("明细", modifier = Modifier.padding(top = 4.dp)) }
                        item {
                            ErpCard {
                                Text(
                                    "明细需要联网才能看。上面这些是从列表里带过来的，"
                                        + "单号、日期、金额都是准的。",
                                    color = LocalErpColors.current.muted,
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

                    if (doc.notes.isNotEmpty()) {
                        item {
                            ErpCard {
                                Text("备注", color = LocalErpColors.current.muted, fontSize = 11.5.sp)
                                Spacer(Modifier.height(6.dp))
                                Text(doc.notes, color = LocalErpColors.current.ink, fontSize = 13.5.sp)
                            }
                        }
                    }
                }
            }
        }
    }
}

/**
 * 展开态抬头卡高度（客户名占**一行**时）。
 *
 * 为什么不精确算成「内容刚好这么高」：字号、行高、字体缩放（系统无障碍字号）一变，
 * 写死的高度就会把最后一行（日期/单号）裁掉一半 —— 2026-10-23 实测正是如此。
 * 现在这一层用 `fillMaxSize()` 顶满折叠容器：容器高多少，卡片就多高，
 * 内容一律顶对齐；容器收起时卡片跟着变矮，这就是折叠动画本身。
 */
private val DocHeaderExpandedHeight: Dp = 138.dp

/**
 * 客户名换成两行时，抬头要多出来的高度。
 *
 * 用户 2026-10-23 报：「客户名字过长的，但不能把单号给挤压没了」。
 * 实测（`宏图建设集团第三项目部`）：名字折成两行后，卡片里那行
 * `10月23日 MD…` 被挤出固定高度、整个消失 —— 用户再也看不到单号。
 *
 * 所以展开高度不能是一个死值：名字占两行时容器要长高一行（21sp 文字约 28dp）。
 * 判定用 `TextMeasurer` **真量**，不按字数猜：中英文混排宽度差得很远，
 * 按字数判定必然有漏（`蓝天幼儿园` 5 个字就够宽，`Andy Fire Supply` 却更宽）。
 */
private val DocHeaderSecondLineExtra: Dp = 28.dp

/** 折叠态高度：一行 `客户名  金额`。 */
private val DocHeaderCollapsedHeight: Dp = 46.dp

/** 折叠所需的滚动距离（px）。 */
private const val DocHeaderCollapsePx = 150f

/**
 * 抬头：展开是完整卡片，折叠是一行。
 *
 * 展开态（回顶时）：
 * ```
 * [销售单] 正式保存
 * 陈老板商行
 * 10月23日  MD202606230002
 * ──────────────────────
 * ¥557.46                      ← 与类型标签同色
 * ```
 * 折叠态：`销售单 陈老板商行　　¥557.46`（字号统一压到 15sp，仍保留类型色）。
 *
 * 用高度 lerp + 两层交叉淡入，而不是 `AnimatedContent`：后者切换时换一套布局，
 * 在阈值附近来回滚动会「跳」，看起来像卡顿。
 */
@Composable
private fun DocumentHeader(doc: DocumentDetail, progress: Float) {
    val colors = LocalErpColors.current
    val tint = orderTypeColor(doc.orderType)
    val headShape = RoundedCornerShape(Dimens.radius)

    /*
     * 客户名会不会折成两行 —— **真量**，不猜。
     *
     * 用户 2026-10-23 的核心诉求是「名字长可以，但别把单号挤没了」。
     * 名字两行时卡片必须跟着长高一行，否则 `10月23日 MD…` 会被裁掉。
     *
     * 用 TextMeasurer 按**真实可用宽度**量：可用宽 = 屏幕宽 − 页边距×2 − 卡片内距×2
     * − 金额宽 − 间隔。金额宽同样由测量得出（`¥24,028.58` 和 `¥9.82` 差很多），
     * 不能写死一个「大约」。
     */
    val density = androidx.compose.ui.platform.LocalDensity.current
    val measurer = androidx.compose.ui.text.rememberTextMeasurer()
    val nameStyle = androidx.compose.ui.text.TextStyle(
        fontSize = 21.sp,
        fontWeight = FontWeight.SemiBold,
    )
    val amountStyle = androidx.compose.ui.text.TextStyle(
        fontSize = 24.sp,
        fontWeight = FontWeight.Bold,
    )
    val screenWidthPx = androidx.compose.ui.platform.LocalConfiguration.current.screenWidthDp
    val nameLines = androidx.compose.runtime.remember(doc.customerName, screenWidthPx) {
        with(density) {
            // 与下方布局一一对应：pageGap×2（页面左右）+ 17dp×2（卡片内距）
            // + 12dp（名字与金额的间隔）+ 金额实测宽。
            val cardInnerPx = (Dimens.pageGap * 2 + 17.dp * 2 + 12.dp).toPx()
            val amountPx = measurer.measure(
                androidx.compose.ui.text.AnnotatedString(doc.totalText),
                style = amountStyle,
            ).size.width.toFloat()
            val availablePx = (screenWidthPx.dp.toPx() - cardInnerPx - amountPx).coerceAtLeast(1f)
            measurer.measure(
                androidx.compose.ui.text.AnnotatedString(doc.customerName),
                style = nameStyle,
                constraints = androidx.compose.ui.unit.Constraints(maxWidth = availablePx.toInt()),
            ).lineCount.coerceIn(1, 2)
        }
    }
    val expandedHeight = DocHeaderExpandedHeight +
        if (nameLines > 1) DocHeaderSecondLineExtra else 0.dp

    val height = lerp(expandedHeight, DocHeaderCollapsedHeight, progress)
    val expandedAlpha = (1f - progress * 1.6f).coerceIn(0f, 1f)
    val collapsedAlpha = ((progress - 0.45f) / 0.55f).coerceIn(0f, 1f)

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(height)
            .clipToBounds()
            .padding(horizontal = Dimens.pageGap, vertical = 4.dp),
    ) {
        // ---- 展开态：整张抬头卡 ----
        // 卡片撑满折叠容器（而不是 wrapContent）：容器多高卡片多高，内容顶对齐 ——
        // 这样既不会把日期行裁掉，卡片下方也不会留一条比页间距还大的空白。
        Box(
            modifier = Modifier
                .fillMaxSize()
                .alpha(expandedAlpha),
        ) {
            ErpCard(modifier = Modifier.fillMaxSize(), padding = 0.dp) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(headShape)
                        // 抬头铺一层极淡的类型色底：余光一扫就能分出单据类型，
                        // 也把抬头和下面的明细卡在视觉上分开。
                        .background(tint.copy(alpha = 0.06f))
                        .padding(horizontal = 17.dp, vertical = 13.dp),
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
                    Spacer(Modifier.height(8.dp))
                    // 客户名 + 金额同一行（用户 2026-10-23 指定：金额放客户名右侧）。
                    //
                    // 名字最多两行、超出省略；金额**不受挤压**（它是这张单的结论，
                    // 被名字挤成两行或截断都是错的）。日期/单号在下一行，
                    // 抬头高度已按名字行数算过，所以不会把它挤没。
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = doc.customerName,
                            color = colors.ink,
                            fontSize = 21.sp,
                            fontWeight = FontWeight.SemiBold,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f),
                        )
                        Spacer(Modifier.width(12.dp))
                        // 金额颜色 = 类型标签颜色（用户要求）。负数（退货/退拿货）用类型色
                        // 之后不再额外染红，符号本身已经在文案里。
                        AmountText(
                            text = doc.totalText,
                            cents = doc.totalCents,
                            size = 24,
                            weight = FontWeight.Bold,
                            tint = tint,
                        )
                    }
                    Spacer(Modifier.height(5.dp))
                    Text(
                        "${doc.dateLabel}　${doc.orderNo}",
                        color = colors.muted,
                        fontSize = 12.sp,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }

        // ---- 折叠态：一行 ----
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(DocHeaderCollapsedHeight)
                .align(Alignment.CenterStart)
                .alpha(collapsedAlpha)
                .padding(horizontal = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = doc.typeLabel,
                color = tint,
                fontSize = 11.5.sp,
                fontWeight = FontWeight.Medium,
                modifier = Modifier
                    .clip(RoundedCornerShape(6.dp))
                    .background(tint.copy(alpha = 0.12f))
                    .padding(horizontal = 6.dp, vertical = 3.dp),
            )
            Spacer(Modifier.width(9.dp))
            // 名字吃掉剩余宽度、最多一行省略；金额不参与挤压（和展开态同一规矩）。
            Text(
                text = doc.customerName,
                color = colors.ink,
                fontSize = 15.sp,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(10.dp))
            AmountText(
                text = doc.totalText,
                cents = doc.totalCents,
                size = 15,
                weight = FontWeight.SemiBold,
                tint = tint,
            )
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
