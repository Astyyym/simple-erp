package com.simpleerp.mobile.ui

import android.content.Intent
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.FileProvider
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
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
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
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.simpleerp.mobile.ui.components.ErpHairline
import com.simpleerp.mobile.ui.components.LoadingState
import com.simpleerp.mobile.ui.components.PrimaryButton
import com.simpleerp.mobile.ui.components.ResetScrollOnSignal
import com.simpleerp.mobile.ui.screens.ConnectionLostScreen
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

    /*
     * 回到前台自动刷新（2026-10-24 加，2026-10-31 补「刷当前页」）。
     *
     * 为什么必须有：`bootstrap()` 只在 ViewModel 的 `init` 里跑一次，而 ViewModel
     * 活得比 Activity 久得多 —— 店主早上开店、点亮屏幕，看到的可能是昨晚关电脑前的
     * 数据，界面上却没有任何迹象说明它是旧的。
     *
     * 为什么用 ON_RESUME 而不是每次重组：重组在打字、滚动时都会发生；
     * 「回到前台」才是「用户重新开始使用」这个语义。
     *
     * 2026-10-31 用户要求「切回应用要自动刷新」：原来这里只调 `maybeAutoSync()`，
     * 它 10 分钟才跑一次全量快照，切出去回来看的还是刚才那份列表。
     * 现在交给 `onAppForeground()`：先刷当前页（一个请求），快照仍按 10 分钟节流。
     */
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) vm.onAppForeground()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    SimpleErpTheme(mode = themeMode) {
        val colors = LocalErpColors.current
        val bootstrapped by vm.bootstrapped.collectAsStateWithLifecycle()
        val needsSetup by vm.needsSetup.collectAsStateWithLifecycle()
        val needsPairing by vm.needsPairing.collectAsStateWithLifecycle()
        val connectionLost by vm.connectionLost.collectAsStateWithLifecycle()
        val connectionDetail by vm.connectionDetail.collectAsStateWithLifecycle()
        val cacheNewest by vm.cacheNewest.collectAsStateWithLifecycle()
        val baseUrl by vm.baseUrl.collectAsStateWithLifecycle()
        // 配对提交进行中：配对页那颗按钮据此禁用（A11，防连点）。
        val pairBusy by vm.pairBusy.collectAsStateWithLifecycle()

        var overlay by remember { mutableStateOf<Overlay?>(null) }
        var pairError by remember { mutableStateOf<String?>(null) }
        var setupForChange by remember { mutableStateOf(false) }

        // ---- 同伴数据包（2026-11-21）----
        //
        // 导出：ViewModel 把 JSON 写进 `cacheDir/peer-packs/`，通过 `shareFile`
        // 交出来；这里换成 FileProvider 的 content:// URI，再丢给系统分享面板。
        // 不能直接用 file:// —— Android 7+ 会对它抛 FileUriExposedException。
        //
        // 导入：用系统的文件选择器。走 SAF 而不是自建文件浏览器，
        // 是为了让「微信里保存下来的文件」「下载目录」「网盘」都能被选到。
        val context = LocalContext.current
        val peerMessage by vm.peerMessage.collectAsStateWithLifecycle()
        val shareFile by vm.shareFile.collectAsStateWithLifecycle()

        val importLauncher = rememberLauncherForActivityResult(
            ActivityResultContracts.OpenDocument(),
        ) { uri ->
            // 用户取消（uri == null）什么都不做：这不是失败。
            if (uri != null) vm.importPack(uri)
        }
        val onImportPack: () -> Unit = {
            // 有些文件管理器/微信给的 MIME 是 octet-stream，只写 application/json 会选不中。
            // 打开本应用的 file_paths 目录不必受限，所以这里接受全部类型，校验交给 PeerPack。
            importLauncher.launch(arrayOf("*/*"))
        }

        LaunchedEffect(shareFile) {
            val file = shareFile ?: return@LaunchedEffect
            val sent = runCatching {
                val uri = FileProvider.getUriForFile(
                    context,
                    "${context.packageName}.fileprovider",
                    file,
                )
                val send = Intent(Intent.ACTION_SEND).apply {
                    type = "application/json"
                    putExtra(Intent.EXTRA_STREAM, uri)
                    // 只给这一个 URI 读权限，并且只在这次分享里有效。
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                context.startActivity(Intent.createChooser(send, "发送数据包"))
            }
            if (sent.isFailure) {
                // 分享面板起不来（极少数 ROM），退回"文件已生成"的提示，
                // 至少告诉用户东西在哪，而不是静默无反应。
                vm.reportPeerError("打不开分享面板：${sent.exceptionOrNull()?.message ?: "未知原因"}")
            }
            vm.consumeShareFile()
        }

        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(colors.bg)
                .statusBarsPadding()
                .navigationBarsPadding(),
        ) {
            // 页面判定是纯函数（`RootRouting`），单测里逐格钉组合矩阵 ——
            // 「改地址点了等于没点」那次死状态就是因为这里的分支漏读了一个标志（A1）。
            when (RootRouting.screen(
                bootstrapped = bootstrapped,
                needsSetup = needsSetup,
                setupForChange = setupForChange,
                connectionLost = connectionLost,
                needsPairing = needsPairing,
            )) {
                RootScreen.LOADING -> LoadingState()

                RootScreen.SETUP -> SetupScreen(
                    // `setupForChange` 在**提交走完 bootstrap 之后**才清：
                    // 期间 `bootstrapped` 已是 false，界面显示 LoadingState，
                    // 不会闪一下「旧页面」再跳走。
                    onSubmit = { address -> vm.saveBaseUrl(address) { setupForChange = false } },
                    // 新手机没有电脑地址时也能从这里导入同伴的数据包 ——
                    // 否则它进不了主界面，也就永远够不到设置页那颗导入按钮。
                    onImportPack = onImportPack,
                    // 取消出口（A1）：只有「主动改地址」且手上还有可用地址时才有，
                    // 取消不清任何东西 —— 因为进这一页时也没清任何东西。
                    onCancel = if (RootRouting.canCancelSetup(setupForChange, baseUrl)) {
                        { setupForChange = false }
                    } else {
                        null
                    },
                )

                /*
                 * 「连不上电脑」（2026-10-31）：本地没确认过配对、又探测不到电脑时停在这里。
                 *
                 * **不要**把它和 needsPairing 合并：后者是「电脑在、但这台手机没被允许」，
                 * 处置方式是输 6 位码；这里是「压根没连上」，处置方式是查 WiFi / 地址 / 防火墙。
                 * 合成一个页面，用户会按错误的步骤排查（2026-10-31 门店事故就是这么发生的）。
                 */
                RootScreen.CONNECTION_LOST -> ConnectionLostScreen(
                    address = baseUrl,
                    detail = connectionDetail,
                    cacheAt = cacheNewest,
                    onRetry = { vm.recheck() },
                    // 「改地址」只是打开配置页（A1）。原来这里调 `saveBaseUrl("")`：
                    // 地址一清、cookie 与「确认过配对」一并作废，而 `needsSetup` 又被
                    // bootstrap 按回 false —— 既看不到输入框也不在主界面，只能杀进程。
                    onChangeAddress = { setupForChange = true },
                    onShowCache = { vm.showOfflineCache() },
                )

                RootScreen.PAIR -> PairScreen(
                    address = baseUrl,
                    error = pairError,
                    busy = pairBusy,
                    // 「先看离线缓存」（A2）：本地有数据才给这颗次级入口，
                    // 样式与「连不上电脑」页那颗一致（灰字、放在主按钮下面）。
                    cacheAt = cacheNewest,
                    onShowCache = { vm.showOfflineCache() },
                    onSubmit = { code ->
                        vm.submitPairCode(code) { error ->
                            pairError = error
                        }
                    },
                    onChangeAddress = {
                        pairError = null
                        // 只进配置页，不清任何东西（A1）。
                        setupForChange = true
                    },
                )

                RootScreen.MAIN -> MainScaffold(
                    vm = vm,
                    overlay = overlay,
                    onOverlay = { overlay = it },
                    onChangeAddress = { setupForChange = true },
                    onImportPack = onImportPack,
                )
            }

            // 同伴数据包的结果提示（2026-11-21）。
            //
            // 用弹窗而不是 Toast：导入失败的原因（版本对不上 / 文件不是数据包）
            // 需要用户读，Toast 两秒就没了，他只会再点一次导入再失败一次。
            val peerMessageText = peerMessage
            if (peerMessageText != null) {
                AlertDialog(
                    onDismissRequest = { vm.consumePeerMessage() },
                    confirmButton = {
                        TextButton(onClick = { vm.consumePeerMessage() }) {
                            Text(
                                "知道了",
                                color = colors.primary,
                                fontSize = 14.sp,
                                fontWeight = FontWeight.Medium,
                            )
                        }
                    },
                    text = { Text(peerMessageText, color = colors.ink, fontSize = 13.5.sp) },
                    containerColor = colors.surface,
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
    onImportPack: () -> Unit,
) {
    val colors = LocalErpColors.current
    val tab by vm.tab.collectAsStateWithLifecycle()
    val refreshing by vm.refreshing.collectAsStateWithLifecycle()

    /*
     * 四个 tab 的列表滚动状态**提升到这里**（2026-10-22 修 N-2/N-3）。
     *
     * 原来 `rememberLazyListState()` 写在各自的 Screen 里：切 tab 时那个 Composable
     * 整个离开组合树，状态被销毁，回来就跳回顶部；进详情页再返回同样如此。
     * 柜台动线是「按拼音滚到某段 → 点开一个客户 → 退回来接着看下一个」，
     * 每次回顶等于每次都要重滚一遍。
     *
     * 放在 MainScaffold 这一层：`MainScaffold` 在 tab 切换与详情进出时始终留在
     * 组合树里（只有内部 when 分支在换），所以这四个状态是稳定的。
     */
    val todayListState = rememberLazyListState()
    val customersListState = rememberLazyListState()
    val ordersListState = rememberLazyListState()
    val productsListState = rememberLazyListState()
    // 商品详情（某商品 → 每个客户价）也有自己的滚动位置与排序回顶需求（2026-10-24）。
    val productDetailListState = rememberLazyListState()

    /*
     * 返回键：
     * - 有详情页 → 关详情（原来的行为，保留）；
     * - 在非「今天」tab → 回到「今天」，**不退出 App**；
     * - 已在「今天」→ 交回系统（退出）。
     *
     * 原来是 `enabled = overlay != null`，于是在客户/单据/商品 tab 上按返回键
     * 直接退出 Activity —— 用户对着门店 App 按返回，期望是「退一层」，不是「关掉」。
     * README 也一直写着「返回键两段行为（详情→列表，首页再按退出）」。
     */
    BackHandler(enabled = overlay != null || tab != Tab.TODAY) {
        if (overlay != null) onOverlay(null) else vm.selectTab(Tab.TODAY)
    }

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
                    val detailQuery by vm.productDetailQuery.collectAsStateWithLifecycle()
                    val detailSortPinyin by vm.productDetailSortPinyin.collectAsStateWithLifecycle()
                    val detailDescending by vm.productDetailDescending.collectAsStateWithLifecycle()
                    val detailScrollReset by vm.productDetailScrollReset.collectAsStateWithLifecycle()
                    LaunchedEffect(current.id) { vm.openProduct(current.id) }
                    // 详情页换排序/方向后回顶：同样要等新顺序上屏（见 ResetScrollOnSignal）。
                    ResetScrollOnSignal(
                        signal = detailScrollReset,
                        listState = productDetailListState,
                        onConsumed = vm::consumeProductDetailScrollReset,
                    )
                    ProductDetailScreen(
                        state = state,
                        query = detailQuery,
                        sortPinyin = detailSortPinyin,
                        descending = detailDescending,
                        listState = productDetailListState,
                        onBack = { onOverlay(null) },
                        onQueryChange = vm::onProductDetailQueryChange,
                        onToggleSort = vm::toggleProductDetailSort,
                        onToggleOrder = vm::toggleProductDetailOrder,
                        onRetry = { vm.openProduct(current.id) },
                    )
                }

                null -> when (tab) {
                    Tab.TODAY -> {
                        val state by vm.today.collectAsStateWithLifecycle()
                        TodayScreen(
                            state = state,
                            listState = todayListState,
                            refreshing = refreshing,
                            onRefresh = { vm.pullRefresh(Tab.TODAY) },
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
                        val scrollReset by vm.customersScrollReset.collectAsStateWithLifecycle()
                        // 换排序 / 换方向后回顶：**等新顺序上屏之后再滚**，否则 LazyColumn
                        // 会按 item key 把「原来的第一行」重新拉回视线，看起来像没回顶
                        // （用户 2026-10-23 真机反馈的原现象）。
                        ResetScrollOnSignal(
                            signal = scrollReset,
                            listState = customersListState,
                            onConsumed = vm::consumeCustomersScrollReset,
                        )
                        CustomersScreen(
                            state = state,
                            query = query,
                            sortDebt = sortDebt,
                            descending = descending,
                            listState = customersListState,
                            refreshing = refreshing,
                            onRefresh = { vm.pullRefresh(Tab.CUSTOMERS) },
                            onQueryChange = vm::onCustomerQueryChange,
                            // 回顶不在这里做：见上面 ResetScrollOnSignal 的说明。
                            onToggleSort = vm::toggleCustomerSort,
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
                        val scrollReset by vm.ordersScrollReset.collectAsStateWithLifecycle()
                        // 换日期/类型/客户后回顶：同样要等新数据上屏（见 ResetScrollOnSignal）。
                        ResetScrollOnSignal(
                            signal = scrollReset,
                            listState = ordersListState,
                            onConsumed = vm::consumeOrdersScrollReset,
                        )
                        OrdersScreen(
                            state = state,
                            orderType = orderType,
                            dateRange = dateRange,
                            customerName = customerName,
                            listState = ordersListState,
                            refreshing = refreshing,
                            onRefresh = { vm.pullRefresh(Tab.ORDERS) },
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
                        val scrollReset by vm.productsScrollReset.collectAsStateWithLifecycle()
                        // 同客户页：新顺序上屏之后再回顶（见 ResetScrollOnSignal 注释）。
                        ResetScrollOnSignal(
                            signal = scrollReset,
                            listState = productsListState,
                            onConsumed = vm::consumeProductsScrollReset,
                        )
                        ProductsScreen(
                            state = state,
                            query = query,
                            sortPrice = sortPrice,
                            descending = descending,
                            listState = productsListState,
                            refreshing = refreshing,
                            onRefresh = { vm.pullRefresh(Tab.PRODUCTS) },
                            onQueryChange = vm::onProductQueryChange,
                            onToggleSort = vm::toggleProductSort,
                            onToggleOrder = vm::toggleProductOrder,
                            onOpenProduct = { onOverlay(Overlay.Product(it)) },
                            onRetry = { vm.refreshProducts() },
                        )
                    }

                    Tab.SETTINGS -> {
                        val meta by vm.meta.collectAsStateWithLifecycle()
                        val shopName by vm.shopName.collectAsStateWithLifecycle()
                        val cacheNewest by vm.cacheNewest.collectAsStateWithLifecycle()
                        val themeMode by vm.themeMode.collectAsStateWithLifecycle()
                        val syncState by vm.syncState.collectAsStateWithLifecycle()
                        val lastSyncAt by vm.lastSyncAt.collectAsStateWithLifecycle()
                        val syncMonths by vm.syncMonths.collectAsStateWithLifecycle()
                        val peerImportedAt by vm.peerImportedAt.collectAsStateWithLifecycle()
                        val peerSourceShop by vm.peerSourceShop.collectAsStateWithLifecycle()
                        val peerBusy by vm.peerBusy.collectAsStateWithLifecycle()
                        SettingsScreen(
                            baseUrl = vm.baseUrl.value,
                            meta = meta,
                            shopName = shopName,
                            themeMode = themeMode,
                            cacheNewest = cacheNewest,
                            syncState = syncState,
                            lastSyncAt = lastSyncAt,
                            syncMonths = syncMonths,
                            peerImportedAt = peerImportedAt,
                            peerSourceShop = peerSourceShop,
                            peerBusy = peerBusy,
                            onExportPack = { vm.exportPack() },
                            onImportPack = onImportPack,
                            onSyncNow = { vm.syncNow() },
                            onSetSyncMonths = vm::setSyncMonths,
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
