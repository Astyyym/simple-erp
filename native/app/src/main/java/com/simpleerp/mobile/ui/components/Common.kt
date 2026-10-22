package com.simpleerp.mobile.ui.components

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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors

/**
 * 卡片：与网页版 `.card` 同一套圆角/描边/内距 **+ 投影**。
 *
 * 投影不是装饰，是层级本身（用户 2026-10-22：「颜色太单调了，分不清楚主次，
 * 看哪里都一样」）。原来只有一道 alpha 0.07 的描边，而 `bg #FBFBFD` 与
 * `surface #FFFFFF` 亮度差不到 2%，结果整屏是一张平铺的白纸：
 * 卡片、卡片间隙、页面背景三者视觉上等价，眼睛找不到「一块一块」的结构。
 * 网页版一直有 `--card-shadow`，原生版漏了 —— 补回来两个入口才像同一个产品。
 */
@Composable
fun ErpCard(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    padding: androidx.compose.ui.unit.Dp = Dimens.cardPad,
    elevated: Boolean = true,
    content: @Composable ColumnScopeAlias.() -> Unit,
) {
    val colors = LocalErpColors.current
    val shape = RoundedCornerShape(Dimens.radius)
    val base = modifier
        .fillMaxWidth()
        .then(
            if (elevated) {
                Modifier.shadow(
                    elevation = 6.dp,
                    shape = shape,
                    ambientColor = colors.cardShadow,
                    spotColor = colors.cardShadow,
                )
            } else {
                Modifier
            }
        )
        .clip(shape)
        .background(colors.surface, shape)
        // 卡片**不加黑描边**（用户 2026-10-22 二次定稿，截图框掉了整片列表）：
        // 列表有十几二十张卡，每张都套一圈黑框会糊成一片网格，压过内容本身。
        // 卡片的「块感」交给投影（上面 6dp shadow）和 surface 与 bg 的色差，
        // 黑描边只留给**控件**（搜索框、chips、按钮）—— 那些是「可以点的地方」，
        // 需要一眼看出来边界。
        .border(1.dp, colors.border, shape)
    val clickable = if (onClick != null) base.clickable(onClick = onClick) else base
    Column(modifier = clickable.padding(padding)) { content() }
}

typealias ColumnScopeAlias = androidx.compose.foundation.layout.ColumnScope

/**
 * 金额文本：**字符串由服务端给，这里只管上色。**
 *
 * 默认规则：负数（多付）用 danger。正数默认 ink，但列表页会显式传 [tint]
 * 把它染成 debt 暖色 —— 欠款是店主扫列表时唯一真正要看的数，
 * 跟客户名同色同号就等于没有重点（用户 2026-10-22 的「分不清主次」）。
 */
@Composable
fun AmountText(
    text: String,
    cents: Long,
    modifier: Modifier = Modifier,
    size: Int = 15,
    weight: FontWeight = FontWeight.Medium,
    tint: Color? = null,
) {
    val colors = LocalErpColors.current
    Text(
        text = text,
        modifier = modifier,
        color = tint ?: if (cents < 0) colors.danger else colors.ink,
        fontSize = size.sp,
        fontWeight = weight,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
    )
}

/**
 * 单据类型的主色。
 *
 * 抽出来是因为**标签底色和金额颜色必须是同一个色**（用户 2026-10-22 要求
 * 「单据右侧的总和数字，颜色也改成和单据类型标签一样的颜色」）。
 * 之前两处各写一份 when，改一处忘另一处就会不同步。
 */
@Composable
fun orderTypeColor(orderType: String): Color {
    val colors = LocalErpColors.current
    return when (orderType) {
        "sale" -> colors.primary
        "return" -> colors.danger
        "purchase" -> colors.warn
        else -> colors.muted
    }
}

/** 类型徽章：销售/退货/拿货/退拿货 用不同色，扫一眼能分清方向。 */
@Composable
fun TypeBadge(label: String, orderType: String) {
    val colors = LocalErpColors.current
    val tint = orderTypeColor(orderType)
    val bg = when (orderType) {
        "sale" -> colors.primarySoft
        "return" -> colors.danger.copy(alpha = 0.12f)
        "purchase" -> colors.warnBg
        else -> colors.soft
    }
    Text(
        text = label,
        color = tint,
        fontSize = 11.sp,
        fontWeight = FontWeight.Medium,
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(bg)
            .padding(horizontal = 7.dp, vertical = 3.dp),
    )
}

/** 空状态：与网页版 `_states.html` 的文案口径一致，可区分「没数据」和「没配对」。 */
@Composable
fun EmptyState(title: String, hint: String = "", modifier: Modifier = Modifier) {
    val colors = LocalErpColors.current
    Column(
        modifier = modifier.fillMaxWidth().padding(vertical = 56.dp, horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(title, color = colors.muted, fontSize = 15.sp, textAlign = TextAlign.Center)
        if (hint.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text(hint, color = colors.muted.copy(alpha = 0.75f), fontSize = 12.5.sp, textAlign = TextAlign.Center)
        }
    }
}

@Composable
fun LoadingState(modifier: Modifier = Modifier) {
    Box(modifier = modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(
            modifier = Modifier.size(28.dp),
            color = LocalErpColors.current.primary,
            strokeWidth = 2.5.dp,
        )
    }
}

/**
 * 错误状态：**必须把「连不上」和「服务端报错」分开写**。
 * 前者用户能自己救（开电脑、连 WiFi），后者只能等或找维护的人。
 */
@Composable
fun ErrorState(
    message: String,
    modifier: Modifier = Modifier,
    actionLabel: String = "重试",
    onAction: (() -> Unit)? = null,
) {
    val colors = LocalErpColors.current
    Column(
        modifier = modifier.fillMaxWidth().padding(vertical = 48.dp, horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(message, color = colors.ink, fontSize = 14.5.sp, textAlign = TextAlign.Center)
        if (onAction != null) {
            Spacer(Modifier.height(18.dp))
            PrimaryButton(text = actionLabel, onClick = onAction)
        }
    }
}

@Composable
fun PrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val colors = LocalErpColors.current
    val shape = RoundedCornerShape(Dimens.radiusSm)
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(Dimens.minTouch)
            .clip(shape)
            .background(if (enabled) colors.primary else colors.muted.copy(alpha = 0.4f), shape)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = colors.primaryInk, fontSize = 15.sp, fontWeight = FontWeight.Medium)
    }
}

/** 离线提示条：**有缓存数据时必须显示时间**，不能让旧数据冒充实时数据。 */
@Composable
fun OfflineBanner(fetchedAtLabel: String, modifier: Modifier = Modifier) {
    val colors = LocalErpColors.current
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(Dimens.radiusSm))
            .background(colors.warnBg)
            .border(1.dp, colors.warnBorder, RoundedCornerShape(Dimens.radiusSm))
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("离线", color = colors.warn, fontSize = 11.sp, fontWeight = FontWeight.Bold)
        Text(
            "连不上电脑，显示的是 $fetchedAtLabel 的数据",
            color = colors.warn,
            fontSize = 12.5.sp,
            maxLines = 2,
        )
    }
}

/**
 * 分段选择器：**N 个 chip 并排、只有一个亮**。
 *
 * 为什么要有这个组件（用户 2026-10-22 报的第一个问题）：
 * 客户页原来是**一个开关按钮** —— 文字在「拼音」和「欠款」之间切换，
 * 只在「欠款」时变蓝。于是「拼音」状态下那颗按钮是灰底的，
 * 跟旁边正序/倒序箭头长得一模一样，看起来像「三个都没选中」，
 * 用户根本不知道当前按什么排序。而网页版一直是两颗 chip 并排、选中那颗标蓝。
 *
 * 换成「并排 + 高亮当前项」后，任何时刻都恰好有一个亮着 ——
 * 选中态是**位置**问题，不是「按钮有没有变蓝」的问题，猜错不了。
 */
@Composable
fun SegmentedChips(
    options: List<String>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
    height: androidx.compose.ui.unit.Dp = 40.dp,
) {
    val colors = LocalErpColors.current
    val outerShape = RoundedCornerShape(Dimens.radiusSm)
    Row(
        modifier = modifier
            .clip(outerShape)
            .background(colors.soft)
            .border(1.dp, colors.border, outerShape)
            .padding(3.dp),
        horizontalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        options.forEachIndexed { index, label ->
            val on = index == selectedIndex
            Box(
                modifier = Modifier
                    .height(height - 6.dp)
                    .clip(RoundedCornerShape(Dimens.radiusSm - 3.dp))
                    .background(if (on) colors.surface else Color.Transparent)
                    .then(
                        if (on) {
                            Modifier.shadow(
                                elevation = 2.dp,
                                shape = RoundedCornerShape(Dimens.radiusSm - 3.dp),
                                ambientColor = colors.cardShadow,
                                spotColor = colors.cardShadow,
                            )
                        } else {
                            Modifier
                        }
                    )
                    .clickable(onClick = { onSelect(index) })
                    .padding(horizontal = 13.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = label,
                    color = if (on) colors.primary else colors.muted,
                    fontSize = 13.sp,
                    fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal,
                )
            }
        }
    }
}

/**
 * 正序/倒序切换箭头。
 *
 * 用箭头而不是「正序/倒序」四个字：搜索框 + 排序 chips + 方向三个控件挤在一行，
 * 中文字太占宽会把搜索框压扁；箭头是通用符号，一眼能认。
 * 箭头方向就是当前结果的实际顺序：↑ = 从小到大 / A→Z，↓ = 反过来。
 */
@Composable
fun OrderChip(descending: Boolean, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    val shape = RoundedCornerShape(Dimens.radiusSm)
    Box(
        modifier = Modifier
            .height(Dimens.minTouch)
            .clip(shape)
            .background(colors.soft)
            .border(1.dp, colors.border, shape)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = if (descending) "↓" else "↑",
            color = colors.ink,
            fontSize = 17.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}

/** 分隔线：网页版用 1px rgba 描边，这里保持一致。 */
@Composable
fun ErpHairline(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(1.dp)
            .background(LocalErpColors.current.border)
    )
}

/** 段标题：`今天 / 卖货 / 进货` 那种小节头。 */
@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier, trailing: @Composable (() -> Unit)? = null) {
    val colors = LocalErpColors.current
    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text, color = colors.muted, fontSize = 11.5.sp, fontWeight = FontWeight.Medium)
        Spacer(Modifier.width(8.dp))
        Box(Modifier.weight(1f).height(1.dp).background(colors.border))
        if (trailing != null) {
            Spacer(Modifier.width(8.dp))
            trailing()
        }
    }
}

/** 键值行：左标签右值，列表里大量复用。 */
@Composable
fun LabelValueRow(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: Color? = null,
    valueSize: Int = 14,
    onClick: (() -> Unit)? = null,
) {
    val colors = LocalErpColors.current
    val base = modifier.fillMaxWidth()
    Row(
        modifier = if (onClick != null) base.clickable(onClick = onClick).padding(vertical = 6.dp) else base.padding(vertical = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = colors.muted, fontSize = 13.5.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(
            value,
            color = valueColor ?: colors.ink,
            fontSize = valueSize.sp,
            fontWeight = FontWeight.Medium,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}
