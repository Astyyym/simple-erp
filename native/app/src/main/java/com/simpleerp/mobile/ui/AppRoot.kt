package com.simpleerp.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.simpleerp.mobile.ui.components.ErpHairline
import com.simpleerp.mobile.ui.components.LoadingState
import com.simpleerp.mobile.ui.components.PrimaryButton
import com.simpleerp.mobile.ui.screens.CustomerDetailScreen
import com.simpleerp.mobile.ui.screens.CustomersScreen
import com.simpleerp.mobile.ui.screens.DocumentDetailScreen
import com.simpleerp.mobile.ui.screens.OrdersScreen
import com.simpleerp.mobile.ui.screens.PairScreen
import com.simpleerp.mobile.ui.screens.ProductDetailScreen
import com.simpleerp.mobile.ui.screens.ProductsScreen
import com.simpleerp.mobile.ui.screens.SettingsScreen
import com.simpleerp.mobile.ui.screens.SetupScreen
import com.simpleerp.mobile.ui.screens.TodayScreen
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.theme.SimpleErpTheme
import com.simpleerp.mobile.ui.theme.ThemeMode

/**
 * 页面栈：详情页是压在 tab 之上的独立层，不占底部 tab 位。
 *
 * 用一个小枚举 + 手写栈而不是 Navigation-Compose：全应用只有 3 个详情页，
 * 引入导航库要为它维护一套 route 字符串与参数编码，收益为负。
 */
private sealed class Overlay {
    data class Customer(val id: Int) : Overlay()
    data class Document(val orderType: String, val id: Int) : Overlay()
    data class Product(val id: Int) : Overlay()
}

@Composable
fun AppRoot() {
    val vm: AppViewModel = viewModel()
    val themeMode by vm.themeMode.collectAsStateWithLifecycle()

    SimpleErpTheme(mode = themeMode) {
        val colors = LocalErpColors.current
        val bootstrapped by vm.bootstrapped.collectAsStateWithLifecycle()
        val needsSetup by vm.needsSetup.collectAsStateWithLifecycle()
        val needsPairing by vm.needsPairing.collectAsStateWithLifecycle()
        val baseUrl by vm.baseUrl.collectAsStateWithLifecycle()

        var overlay by remember { mutableStateOf<Overlay?>(null) }
        var pairError by remember { mutableStateOf<String?>(null) }
        var setupForChange by remember { mutableStateOf(false) }

        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(colors.bg)
                .statusBarsPadding()
                .navigationBarsPadding(),
        ) {
            when {
                !bootstrapped -> LoadingState()

                needsSetup -> SetupScreen(
                    onSubmit = { address ->
                        vm.saveBaseUrl(address)
                        setupForChange = false
                    },
                )

                needsPairing -> PairScreen(
                    address = baseUrl,
                    error = pairError,
                    onSubmit = { code ->
                        vm.submitPairCode(code) { error ->
                            pairError = error
                        }
                    },
                    onChangeAddress = {
                        pairError = null
                        vm.saveBaseUrl("")
                        setupForChange = true
                    },
                )

                else -> MainScaffold(
                    vm = vm,
                    overlay = overlay,
                    onOverlay = { overlay = it },
                    onChangeAddress = { vm.saveBaseUrl("") },
                )
            }
        }
    }
}

@Composable
private fun MainScaffold(
    vm: AppViewModel,
    overlay: Overlay?,
    onOverlay: (Overlay?) -> Unit,
    onChangeAddress: () -> Unit,
) {
    val colors = LocalErpColors.current
    val tab by vm.tab.collectAsStateWithLifecycle()

    // 有详情页时，返回键先关详情页；在 tab 首页时交给系统退出。
    BackHandler(enabled = overlay != null) { onOverlay(null) }

    Column(modifier = Modifier.fillMaxSize()) {
        Box(modifier = Modifier.weight(1f)) {
            when (val current = overlay) {
                is Overlay.Customer -> {
                    val state by vm.customerDetail.collectAsStateWithLifecycle()
                    LaunchedEffect(current.id) { vm.openCustomer(current.id) }
                    CustomerDetailScreen(
                        state = state,
                        onBack = { onOverlay(null) },
                        onRetry = { vm.openCustomer(current.id) },
                        onOpenDocument = { type, id -> onOverlay(Overlay.Document(type, id)) },
                        onShowAllOrders = { customerId, name ->
                            onOverlay(null)
                            vm.showOrdersForCustomer(customerId, name)
                        },
                    )
                }

                is Overlay.Document -> {
                    val state by vm.documentDetail.collectAsStateWithLifecycle()
                    LaunchedEffect(current.orderType, current.id) {
                        vm.openDocument(current.orderType, current.id)
                    }
                    DocumentDetailScreen(
                        state = state,
                        onBack = { onOverlay(null) },
                        onRetry = { vm.openDocument(current.orderType, current.id) },
                    )
                }

                is Overlay.Product -> {
                    val state by vm.productDetail.collectAsStateWithLifecycle()
                    LaunchedEffect(current.id) { vm.openProduct(current.id) }
                    ProductDetailScreen(
                        state = state,
                        onBack = { onOverlay(null) },
                        onRetry = { vm.openProduct(current.id) },
                    )
                }

                null -> when (tab) {
                    Tab.TODAY -> {
                        val state by vm.today.collectAsStateWithLifecycle()
                        TodayScreen(
                            state = state,
                            onRetry = { vm.refreshToday() },
                            onOpenDocument = { type, id -> onOverlay(Overlay.Document(type, id)) },
                            onShowTypeOrders = { type ->
                                vm.setOrderType(type)
                                vm.selectTab(Tab.ORDERS)
                            },
                        )
                    }

                    Tab.CUSTOMERS -> {
                        val state by vm.customers.collectAsStateWithLifecycle()
                        val query by vm.customerQuery.collectAsStateWithLifecycle()
                        val sortDebt by vm.customerSortDebt.collectAsStateWithLifecycle()
                        val descending by vm.customerDescending.collectAsStateWithLifecycle()
                        CustomersScreen(
                            state = state,
                            query = query,
                            sortDebt = sortDebt,
                            onQueryChange = vm::onCustomerQueryChange,
                            onToggleSort = vm::toggleCustomerSort,
                            descending = descending,
                            onToggleOrder = vm::toggleCustomerOrder,
                            onOpenCustomer = { onOverlay(Overlay.Customer(it.id)) },
                            onRetry = { vm.refreshCustomers() },
                        )
                    }

                    Tab.ORDERS -> {
                        val state by vm.orders.collectAsStateWithLifecycle()
                        val orderType by vm.orderType.collectAsStateWithLifecycle()
                        val dateRange by vm.dateRange.collectAsStateWithLifecycle()
                        val customerName by vm.orderCustomerName.collectAsStateWithLifecycle()
                        OrdersScreen(
                            state = state,
                            orderType = orderType,
                            dateRange = dateRange,
                            customerName = customerName,
                            onSelectType = vm::setOrderType,
                            onSelectRange = vm::setDateRange,
                            onClearCustomer = vm::clearOrderCustomer,
                            onOpenDocument = { type, id -> onOverlay(Overlay.Document(type, id)) },
                            onRetry = { vm.refreshOrders() },
                        )
                    }

                    Tab.PRODUCTS -> {
                        val state by vm.products.collectAsStateWithLifecycle()
                        val query by vm.productQuery.collectAsStateWithLifecycle()
                        val sortPrice by vm.productSortPrice.collectAsStateWithLifecycle()
                        val descending by vm.productDescending.collectAsStateWithLifecycle()
                        ProductsScreen(
                            state = state,
                            query = query,
                            sortPrice = sortPrice,
                            descending = descending,
                            onQueryChange = vm::onProductQueryChange,
                            onToggleSort = vm::toggleProductSort,
                            onToggleOrder = vm::toggleProductOrder,
                            onOpenProduct = { onOverlay(Overlay.Product(it)) },
                            onRetry = { vm.refreshProducts() },
                        )
                    }

                    Tab.SETTINGS -> {
                        val meta by vm.meta.collectAsStateWithLifecycle()
                        val cacheNewest by vm.cacheNewest.collectAsStateWithLifecycle()
                        val themeMode by vm.themeMode.collectAsStateWithLifecycle()
                        SettingsScreen(
                            baseUrl = vm.baseUrl.value,
                            meta = meta,
                            themeMode = themeMode,
                            cacheNewest = cacheNewest,
                            onThemeMode = vm::setThemeMode,
                            onChangeAddress = onChangeAddress,
                            onRefresh = { vm.recheck() },
                            onDisconnect = { vm.disconnect() },
                        )
                    }
                }
            }
        }

        if (overlay == null) {
            BottomTabBar(
                current = tab,
                onSelect = { vm.selectTab(it) },
            )
        }
    }
}

/**
 * 底部 5 个 tab。
 *
 * 设置放在第 5 个是用户 2026-10-17 定案：可发现性优先于频次 ——
 * 藏进右上角齿轮的话，出问题（IP 变了、要换电脑）时没人找得到。
 */
@Composable
private fun BottomTabBar(current: Tab, onSelect: (Tab) -> Unit) {
    val colors = LocalErpColors.current
    Column {
        // **不要在这里加任何分隔线**（用户 2026-10-22 明确要求删掉）。
        // 浅色下纯黑 1px 像一条硬边框，深色下换成白色又变成一道亮线，
        // 两种模式都抢眼。tab 条和内容区的分界交给 tab 条自己的底色 + 投影。
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(colors.surface)
                .padding(vertical = 6.dp),
            horizontalArrangement = Arrangement.SpaceEvenly,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Tab.entries.forEach { entry ->
                TabItem(
                    label = entry.label,
                    selected = entry == current,
                    onClick = { onSelect(entry) },
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }
}

/**
 * 单个 tab。
 *
 * 选中态**原来只有一条 3dp 的细横线 + 文字变蓝**，用户 2026-10-22 报「不明显」——
 * 那条线太细，5 个 tab 平分屏宽后每格又窄，几乎看不见；文字蓝与灰在深色下对比也弱。
 *
 * 现在改成「胶囊底色」：选中项整格铺一层 primarySoft 圆角底，
 * 文字用 primary + SemiBold，未选中只有灰字。**面积**的差异比一条线好认得多，
 * 余光扫一眼就知道在哪一页。
 */
@Composable
private fun TabItem(
    label: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    Column(
        modifier = modifier
            .clickable(onClick = onClick)
            .padding(horizontal = 6.dp, vertical = 5.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(9.dp))
                .background(if (selected) colors.primarySoft else androidx.compose.ui.graphics.Color.Transparent)
                .padding(vertical = 7.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = label,
                color = if (selected) colors.primary else colors.muted,
                fontSize = if (selected) 11.5.sp else 11.sp,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}
