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
import com.simpleerp.mobile.data.Customer
import com.simpleerp.mobile.ui.UiState
import com.simpleerp.mobile.ui.components.AmountText
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
 * 客户列表。
 *
 * 金额按用户 2026-10-17 晚定案**显示精确数字**（不再分档）：
 * 「标签不要写小于一千、一千到1万，直接把它具体数字写出来」。
 * 隐私由店主自行承担，将来若要恢复分档必须重新征得同意 —— 别自己改回去。
 *
 * 排序两个：拼音（默认，柜台按店名首字母找最快）/ 欠款从多到少。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun CustomersScreen(
    state: UiState<List<Customer>>,
    query: String,
    sortDebt: Boolean,
    descending: Boolean,
    /**
     * 列表滚动状态**由上层持有**（2026-10-22 修）。
     *
     * 原来在函数里 `rememberLazyListState()`：切 tab 时整个 Composable 被销毁重建，
     * 状态跟着丢，滚到第 20 条再切回来就跳回顶部 —— 柜台「找到客户→看一眼→回来
     * 接着找下一个」的动线被反复打断。详情页返回同理。
     */
    listState: LazyListState,
    refreshing: Boolean,
    onRefresh: () -> Unit,
    onQueryChange: (String) -> Unit,
    onToggleSort: () -> Unit,
    onToggleOrder: () -> Unit,
    onOpenCustomer: (Customer) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    /*
     * 注意：**这里不能放 `LaunchedEffect(sortDebt, descending) { scrollToItem(0) }`**。
     *
     * 那个写法看着对，实际有两个问题（2026-10-22 实测踩到）：
     * 1. `LaunchedEffect` 在 Composable **重新进入组合**时也会跑一遍 ——
     *    切 tab 回来就等于又执行一次 scrollToItem(0)，滚动位置必然丢；
     * 2. 所以「切 tab 丢位置」不是单纯的 state 提升问题，回顶逻辑本身就是元凶之一。
     *
     * 现在「换排序回顶」在 AppRoot 的排序回调里做（只响应用户点击，且能拿到
     * 提升上来的 listState），这里不再自动回顶。
     */
    Column(modifier = modifier.fillMaxWidth()) {
        // ---- 搜索 + 排序 ----
        // 用 FlowRow：窄屏（≤360dp）放不下「搜索框 + 两个排序 chip + 方向箭头」时
        // 自动换行，而不是把搜索框压成一条缝或把箭头顶出屏幕（2026-10-22 实测 945px
        // 已贴边，360dp 机型会直接裁掉）。
        FlowRow(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = Dimens.pageGap, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SearchField(
                value = query,
                // 短占位符：这一行还要塞排序 chips 和方向箭头，
                // 原来 9 个字在窄屏会被压成两行，搜索框高度跟着撑破
                // （2026-10-22 实测 945px 宽下「母」字掉到第二行）。
                placeholder = "搜客户或首字母",
                onValueChange = onQueryChange,
                modifier = Modifier.weight(1f),
            )
            // 两颗并排、选中那颗标蓝。原来是一颗「拼音/欠款」文字互变的开关按钮，
            // 在「拼音」状态下是灰底，看起来像「没选中任何一项」（用户 2026-10-22 报）。
            SegmentedChips(
                options = listOf("拼音", "欠款"),
                selectedIndex = if (sortDebt) 1 else 0,
                onSelect = { index ->
                    // 只在真的换了排序方式时才回调：点已选中的那颗不该重置方向。
                    if ((index == 1) != sortDebt) onToggleSort()
                },
            )
            OrderChip(descending = descending, onClick = onToggleOrder)
        }

        when (state) {
            is UiState.Loading -> LoadingState()
            is UiState.Failed -> ErrorState(state.message, onAction = onRetry)
            UiState.NeedsPairing, UiState.NeedsSetup -> LoadingState()
            is UiState.Ready -> {
                val customers = state.value
                Refreshable(refreshing = refreshing, onRefresh = onRefresh) {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(
                            start = Dimens.pageGap,
                            end = Dimens.pageGap,
                            bottom = 28.dp,
                        ),
                        verticalArrangement = Arrangement.spacedBy(0.dp),
                    ) {
                        if (state.fromCache) {
                            item { OfflineBanner(formatCacheTime(state.fetchedAt)) }
                        }
                        if (customers.isEmpty()) {
                            item {
                                EmptyState(
                                    title = if (query.isBlank()) "还没有客户" else "没找到这个客户",
                                    hint = if (query.isBlank()) "" else "换个关键词，或只输入首字母试试。",
                                )
                            }
                        } else {
                            /*
                             * 视觉上仍是「一整张卡 + 内部行 + 发丝线」，但**每行是一个独立 item**
                             * （2026-10-22 修 N-2 的关键）。
                             *
                             * 原来的写法是「一个 item 里塞一整个 Column（几十行）」：
                             * 切 tab 后 LazyColumn 重新测量时，这个单 item 的瞬时高度不可知，
                             * 滚动偏移会被夹到 0 —— 实测 `offset` 明明存着 3575，画面却回顶部。
                             * 拆成每行一个 item 后，LazyColumn 按行恢复位置，稳定不丢。
                             *
                             * 卡片外观用 ErpCard 铺在每行背后：第一行圆上角、最后一行圆下角，
                             * 中间行直角，视觉上与一张整卡完全一致。
                             */
                            customers.forEachIndexed { index, customer ->
                                item(key = customer.id) {
                                    ErpCard(
                                        padding = 0.dp,
                                        elevated = index == 0,
                                        bordered = false,
                                        shape = listRowShape(
                                            isFirst = index == 0,
                                            isLast = index == customers.lastIndex,
                                        ),
                                        modifier = Modifier.listRowBorder(
                                            colors = colors,
                                            isFirst = index == 0,
                                            isLast = index == customers.lastIndex,
                                        ),
                                    ) {
                                        CustomerRow(
                                            customer = customer,
                                            onClick = { onOpenCustomer(customer) },
                                        )
                                        if (index != customers.lastIndex) {
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

@Composable
private fun SearchField(
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
                modifier = Modifier
                    .clickable { onValueChange("") }
                    .padding(start = 8.dp),
            )
        }
    }
}

/**
 * 一行客户。**三级层次，不能平**：
 *
 * 1. 客户名 —— 16sp Medium，主体，店主找的就是这个名字；
 * 2. 金额   —— 19sp SemiBold + debt 暖色，扫列表时真正要看的数，
 *              必须比名字更抓眼，否则「看哪里都一样」；
 * 3. 电话/状态 —— 11.5sp muted，辅助信息。
 *
 * 原来是「名字 15sp Medium」+「金额 15sp SemiBold」同级同色（都是 ink），
 * 加上状态字只是灰色小字，整行没有焦点（用户 2026-10-22 报的主问题）。
 * 状态从裸文字改成带底色的徽章：欠款/多付/已结清三者一眼分得开，
 * 且不再需要用户去猜「这个数字是欠他还是他欠我」。
 */
@Composable
private fun CustomerRow(customer: Customer, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    val owed = customer.balanceCents > 0

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = customer.name,
                color = colors.ink,
                fontSize = 15.sp,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            // 副标题：电话。网页版还带拼音，但原生端列表接口没给 pinyin 字段，
            // 这里就只放电话 —— 不为了对齐结构去后端加字段，那是另一件事。
            if (customer.phone.isNotEmpty()) {
                Spacer(Modifier.height(3.dp))
                Text(
                    customer.phone,
                    color = colors.muted,
                    fontSize = 11.5.sp,
                    maxLines = 1,
                )
            }
        }
        Spacer(Modifier.width(10.dp))
        // 金额：**别人欠我的（正数）用绿色**，多付（负数）走 danger。
        //
        // 2026-10-23 用户要求改色：原来正数用 debt 暖橙，一眼看过去像「告警」，
        // 但「客户欠我钱」在账本语义里是**应收、是好事**，应该用绿色。
        // 负数是「我欠他/多收了」才用红。正负号 + 颜色已说清楚，不再贴徽章。
        AmountText(
            text = customer.balanceText,
            cents = customer.balanceCents,
            size = 16,
            weight = FontWeight.SemiBold,
            tint = if (owed) colors.success else null,
        )
    }
}
