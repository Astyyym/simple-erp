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
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.ErpColors
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
    bordered: Boolean = true,
    shape: androidx.compose.ui.graphics.Shape = RoundedCornerShape(Dimens.radius),
    content: @Composable ColumnScopeAlias.() -> Unit,
) {
    val colors = LocalErpColors.current
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
        // 卡片默认**不加黑描边**（用户 2026-10-22 二次定稿：每张卡都套黑框会糊成网格）。
        // 但「每行一个 item 的列表」需要靠描边补回整卡轮廓，见 listRowShape 的说明，
        // 那种场景传 bordered = true。
        .then(if (bordered) Modifier.border(1.dp, colors.border, shape) else Modifier)
    val clickable = if (onClick != null) base.clickable(onClick = onClick) else base
    Column(modifier = clickable.padding(padding)) { content() }
}

typealias ColumnScopeAlias = androidx.compose.foundation.layout.ColumnScope

/**
 * 列表行卡片形状：**首行圆上角、末行圆下角、中间行直角**。
 *
 * 为什么需要这个：为了修「切 tab 丢滚动位置」，列表必须**每行一个 LazyColumn item**
 * （一整个 Column 塞进单个 item 时，重新测量会把滚动偏移夹到 0，实测 offset 存着
 * 3575 画面却回顶部）。但视觉上仍要像「一整张卡 + 内部发丝线」，
 * 所以每个 item 背后铺一张 ErpCard，用形状拼接出整卡的圆角轮廓。
 */
fun listRowShape(isFirst: Boolean, isLast: Boolean): androidx.compose.ui.graphics.Shape {
    val r = Dimens.radius
    return RoundedCornerShape(
        topStart = if (isFirst) r else 0.dp,
        topEnd = if (isFirst) r else 0.dp,
        bottomStart = if (isLast) r else 0.dp,
        bottomEnd = if (isLast) r else 0.dp,
    )
}

/**
 * 列表行卡片的描边：**只画整卡的外框，不画行间接缝**。
 *
 * 每行一个 item 后，如果每行都 `border(...)` 四边，行与行之间会出现一条条横线，
 * 整卡被切成一段段（2026-10-22 实测截图：商品列表每行一个框，像表格不像卡）。
 * 这里只补最外侧：首行画上边、末行画下边，所有行画左右两边。
 *
 * ## 2026-10-23：描边必须跟着**圆角**走
 *
 * 原来左右两边是两条**从行顶到行底**的直线。首/末行是圆角的（`listRowShape`），
 * 于是直角描边从圆角外面探出来 —— 浅色下 border 只有 alpha 0.07 看不出来，
 * 深色模式下（border 是 0.09 白）就成了一圈「圆角矩形后面的直角方框」，
 * 用户直接圈图报了出来。
 *
 * 现在改成：竖线在圆角起止处收住，圆角用 `drawArc` 补齐，弧度/半径与
 * `Dimens.radius` 和 `ErpCard` 的 shape 完全一致。中间行没有圆角，仍只画竖线。
 */
fun Modifier.listRowBorder(
    colors: ErpColors,
    isFirst: Boolean,
    isLast: Boolean,
): Modifier = this.drawBehind {
    val stroke = 1.dp.toPx()
    // 与 ErpCard 的 `.border(1dp)` 对齐：描边中心落在边缘内侧半像素。
    val inset = stroke / 2f
    val left = inset
    val top = inset
    val right = size.width - inset
    val bottom = size.height - inset
    // 半径必须与 `listRowShape` / `ErpCard` 的圆角一致，否则描边会比卡片更"方"。
    val radius = Dimens.radius.toPx()
    val topRadius = if (isFirst) radius else 0f
    val bottomRadius = if (isLast) radius else 0f
    if (right <= left || bottom <= top) return@drawBehind

    // 左右两条竖线：只在没有圆角的部分画，避免探出圆角外面。
    drawLine(
        colors.border,
        androidx.compose.ui.geometry.Offset(left, top + topRadius),
        androidx.compose.ui.geometry.Offset(left, bottom - bottomRadius),
        stroke,
    )
    drawLine(
        colors.border,
        androidx.compose.ui.geometry.Offset(right, top + topRadius),
        androidx.compose.ui.geometry.Offset(right, bottom - bottomRadius),
        stroke,
    )

    // 首行：上边 + 两个上圆角。
    if (isFirst) {
        drawLine(
            colors.border,
            androidx.compose.ui.geometry.Offset(left + topRadius, top),
            androidx.compose.ui.geometry.Offset(right - topRadius, top),
            stroke,
        )
        if (topRadius > 0f) {
            // 左上角：180° → 270°
            drawArc(
                color = colors.border,
                startAngle = 180f,
                sweepAngle = 90f,
                useCenter = false,
                topLeft = androidx.compose.ui.geometry.Offset(left, top),
                size = androidx.compose.ui.geometry.Size(topRadius * 2, topRadius * 2),
                style = androidx.compose.ui.graphics.drawscope.Stroke(width = stroke),
            )
            // 右上角：270° → 360°
            drawArc(
                color = colors.border,
                startAngle = 270f,
                sweepAngle = 90f,
                useCenter = false,
                topLeft = androidx.compose.ui.geometry.Offset(right - topRadius * 2, top),
                size = androidx.compose.ui.geometry.Size(topRadius * 2, topRadius * 2),
                style = androidx.compose.ui.graphics.drawscope.Stroke(width = stroke),
            )
        }
    }

    // 末行：下边 + 两个下圆角。
    if (isLast) {
        drawLine(
            colors.border,
            androidx.compose.ui.geometry.Offset(left + bottomRadius, bottom),
            androidx.compose.ui.geometry.Offset(right - bottomRadius, bottom),
            stroke,
        )
        if (bottomRadius > 0f) {
            // 左下角：90° → 180°
            drawArc(
                color = colors.border,
                startAngle = 90f,
                sweepAngle = 90f,
                useCenter = false,
                topLeft = androidx.compose.ui.geometry.Offset(left, bottom - bottomRadius * 2),
                size = androidx.compose.ui.geometry.Size(bottomRadius * 2, bottomRadius * 2),
                style = androidx.compose.ui.graphics.drawscope.Stroke(width = stroke),
            )
            // 右下角：0° → 90°
            drawArc(
                color = colors.border,
                startAngle = 0f,
                sweepAngle = 90f,
                useCenter = false,
                topLeft = androidx.compose.ui.geometry.Offset(right - bottomRadius * 2, bottom - bottomRadius * 2),
                size = androidx.compose.ui.geometry.Size(bottomRadius * 2, bottomRadius * 2),
                style = androidx.compose.ui.graphics.drawscope.Stroke(width = stroke),
            )
        }
    }
}

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
 *
 * 高度默认取 [Dimens.minTouch]（48dp），**必须和同排的搜索框、方向箭头一致**：
 * 原来默认 40dp，比搜索框矮 8dp，一排三个圆角矩形上沿不齐、下沿也不齐
 * （用户 2026-10-23 两次圈图反馈）。
 */
@Composable
fun SegmentedChips(
    options: List<String>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
    height: androidx.compose.ui.unit.Dp = Dimens.minTouch,
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

/**
 * 下拉刷新容器：**与网页版 mobile.js 的下拉刷新对齐**（README 一直这么写，
 * 但原生端此前漏了 —— 2026-10-22 补齐）。
 *
 * 为什么用 material3 的 `PullToRefreshBox` 而不是自己接手势：
 * 自己写要处理嵌套滚动、阈值、回弹动画，还得和 LazyColumn 抢手势；
 * 官方组件已经把「列表在顶部才触发」这件事做对，样式也跟 App 的圆角语言一致。
 *
 * 只包**列表内容**，不包搜索栏/筛选栏 —— 否则在筛选栏上下拉也会触发刷新，
 * 那不是用户想要的（他们要的是「刷这批数据」）。
 */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun Refreshable(
    refreshing: Boolean,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    androidx.compose.material3.pulltorefresh.PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = onRefresh,
        modifier = modifier.fillMaxWidth(),
    ) {
        content()
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

/**
 * 「新数据上屏之后」再把列表滚回顶部 —— 排序切换回顶的正确姿势。
 *
 * 为什么不能直接在点击回调里 `scrollToItem(0)`（2026-10-23 用户真机反馈
 * 「切换正序倒序之后页面不回顶，而是在原位置刷新」）：
 * `LazyColumn` 在**数据顺序变化时会按 item key 重新锚定**视线。先滚到 0，
 * 紧接着新顺序上屏，它会把「原来第一行」（现在可能排到第 40 行）重新拉回视野，
 * 于是画面停在原来的行号上，看起来像没回顶。
 *
 * 正确顺序：等新顺序真正上屏（`withFrameNanos` 等一帧）→ 滚到 0 → 清掉这个
 * 一次性信号。信号是**事件**而不是状态，所以切 tab 重新进入组合不会重复回顶
 * （2026-10-22 被 `LaunchedEffect(sort, descending)` 坑过：那写法每次重新进入
 * 组合都会跑一遍，等于每次切 tab 都丢位置）。
 */
@Composable
fun ResetScrollOnSignal(
    signal: Boolean,
    listState: androidx.compose.foundation.lazy.LazyListState,
    onConsumed: () -> Unit,
) {
    androidx.compose.runtime.LaunchedEffect(signal) {
        if (!signal) return@LaunchedEffect
        androidx.compose.runtime.withFrameNanos { }
        listState.scrollToItem(0)
        onConsumed()
    }
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
