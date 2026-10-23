package com.simpleerp.mobile.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.simpleerp.mobile.data.Customer
import com.simpleerp.mobile.data.CustomerDetailResponse
import com.simpleerp.mobile.data.CustomerDetail
import com.simpleerp.mobile.data.CustomersResponse
import com.simpleerp.mobile.data.DateRange
import com.simpleerp.mobile.data.DocumentDetail
import com.simpleerp.mobile.data.DocumentDetailResponse
import com.simpleerp.mobile.data.DocumentSummary
import com.simpleerp.mobile.data.MetaResponse
import com.simpleerp.mobile.data.OrdersResponse
import com.simpleerp.mobile.data.PairStatusResponse
import com.simpleerp.mobile.data.ProductDetailResponse
import com.simpleerp.mobile.data.ProductsResponse
import com.simpleerp.mobile.data.TodayOverview
import com.simpleerp.mobile.data.TodayResponse
import com.simpleerp.mobile.net.ApiClient
import com.simpleerp.mobile.net.ApiResult
import com.simpleerp.mobile.net.Loaded
import com.simpleerp.mobile.net.LoadOutcome
import com.simpleerp.mobile.net.PersistentCookieStore
import com.simpleerp.mobile.net.ResponseCache
import com.simpleerp.mobile.net.ServerConfig
import com.simpleerp.mobile.net.loadWithCache
import com.simpleerp.mobile.ui.theme.ThemeMode
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.builtins.serializer

/**
 * 底部导航的目的地。
 *
 * 顺序 = **用户 2026-10-23 指定的排列**：今天 / 单据 / 客户 / 商品 / 设置。
 * 「单据」提到第 2 位（原来是 今天/客户/单据）：门店高频动作是「翻单据核对金额、
 * 报单号」，比「看客户」更靠前；设置仍是第 5 个（可发现性优先于频次）。
 *
 * 枚举顺序就是 tab 条顺序（`Tab.entries` 直接渲染），改顺序只改这里。
 */
enum class Tab(val label: String) {
    TODAY("今天"),
    ORDERS("单据"),
    CUSTOMERS("客户"),
    PRODUCTS("商品"),
    SETTINGS("设置"),
}

/**
 * 下拉刷新指示器的最短可见时长（毫秒）。
 *
 * 局域网请求常常 50ms 内就回来了，指示器一闪而过，用户会以为「手势没生效」
 * 而反复下拉（2026-10-23 真机反馈）。
 */
private const val MIN_REFRESH_VISIBLE_MS = 600L

/** 单个页面的加载状态：三态可区分（加载中 / 有数据 / 失败），不靠猜。 */
sealed class UiState<out T> {
    data object Loading : UiState<Nothing>()
    data class Ready<T>(
        val value: T,
        val fromCache: Boolean = false,
        val fetchedAt: Long = 0L,
    ) : UiState<T>()
    data class Failed(val message: String) : UiState<Nothing>()
    data object NeedsPairing : UiState<Nothing>()
    data object NeedsSetup : UiState<Nothing>()
}

/**
 * 全应用唯一的 ViewModel。
 *
 * 为什么不用每个页面一个 ViewModel：这个 App 的页面之间共享**同一份筛选上下文**
 * （客户详情 → 该客户的单据列表 → 单据详情），拆成多个 VM 就得在外面维护一份
 * 共享状态，反而更容易不一致。页面状态本身就是一个小型导航栈，集中管理更简单。
 */
class AppViewModel(app: Application) : AndroidViewModel(app) {

    private val serverConfig = ServerConfig(app)
    private val cache = ResponseCache(app)
    private val api = ApiClient(app)

    // ---- 全局 ----
    private val _tab = MutableStateFlow(Tab.TODAY)
    val tab: StateFlow<Tab> = _tab.asStateFlow()

    private val _themeMode = MutableStateFlow(ThemeMode.SYSTEM)
    val themeMode: StateFlow<ThemeMode> = _themeMode.asStateFlow()

    private val _baseUrl = MutableStateFlow("")
    val baseUrl: StateFlow<String> = _baseUrl.asStateFlow()

    private val _meta = MutableStateFlow<MetaResponse?>(null)
    val meta: StateFlow<MetaResponse?> = _meta.asStateFlow()

    /**
     * 连到的这台电脑的**公司名称**（服务端 `config.shop_name`）。
     *
     * 设置页顶部原来写死「简单ERP」—— 那是程序名，不是店名。用户要看到
     * 「我现在连的是哪家店」，所以取服务端 meta 的 `shop_name`，
     * 并在本地存一份（断网时兜底，见 `ServerConfig.shopName`）。
     */
    private val _shopName = MutableStateFlow("")
    val shopName: StateFlow<String> = _shopName.asStateFlow()

    /** 需要用户去配对（地址有了但没配对，或服务端撤销了设备）。 */
    private val _needsPairing = MutableStateFlow(false)
    val needsPairing: StateFlow<Boolean> = _needsPairing.asStateFlow()

    private val _needsSetup = MutableStateFlow(false)
    val needsSetup: StateFlow<Boolean> = _needsSetup.asStateFlow()

    private val _bootstrapped = MutableStateFlow(false)
    val bootstrapped: StateFlow<Boolean> = _bootstrapped.asStateFlow()

    // ---- 页面 ----
    private val _today = MutableStateFlow<UiState<TodayOverview>>(UiState.Loading)
    val today: StateFlow<UiState<TodayOverview>> = _today.asStateFlow()

    private val _customers = MutableStateFlow<UiState<List<Customer>>>(UiState.Loading)
    val customers: StateFlow<UiState<List<Customer>>> = _customers.asStateFlow()

    private val _customerQuery = MutableStateFlow("")
    val customerQuery: StateFlow<String> = _customerQuery.asStateFlow()

    private val _customerSortDebt = MutableStateFlow(false)
    val customerSortDebt: StateFlow<Boolean> = _customerSortDebt.asStateFlow()

    /**
     * 客户列表正倒序（2026-10-21 用户要求加切换）。
     *
     * 默认 false = 正序，且**两个排序方向不同**：
     * - 拼音正序 A→Z（柜台按首字母找店）
     * - 欠款正序 少→多（先看小额的，催款从小往大谈）
     *
     * 之所以按「排序方式各自记住默认方向」而不是共用一个布尔：两种排序的
     * 「正序」语义本来就相反，硬统一会让用户切换排序时方向莫名其妙地翻转。
     */
    private val _customerDescending = MutableStateFlow(false)
    val customerDescending: StateFlow<Boolean> = _customerDescending.asStateFlow()

    /**
     * 「换排序后要回顶」的一次性请求。
     *
     * 为什么不在点击回调里直接 `scrollToItem(0)`（2026-10-23 用户真机反馈「切换后
     * 还在原位置刷新」）：LazyColumn 在**数据顺序变化时会按 item key 重新锚定**
     * 当前位置 —— 先滚到 index 0（旧顺序的第一个 key），数据一换，它又把那个 key
     * 拉回视线，于是画面停在原来的行号上，看起来像「没回顶」。
     *
     * 正确顺序是「新顺序上屏之后再回顶」：这里只置一个待办标记，由列表页在
     * 数据真正 Ready 时消费并滚到顶部，滚完清标记。标记是一次性的，所以切 tab
     * 重新进入组合不会重复回顶（那正是 2026-10-22 被 `LaunchedEffect` 坑过的地方）。
     */
    private val _customersScrollReset = MutableStateFlow(false)
    val customersScrollReset: StateFlow<Boolean> = _customersScrollReset.asStateFlow()

    fun consumeCustomersScrollReset() {
        _customersScrollReset.value = false
    }

    private val _customerDetail = MutableStateFlow<UiState<CustomerDetailResponse>>(UiState.Loading)
    val customerDetail: StateFlow<UiState<CustomerDetailResponse>> = _customerDetail.asStateFlow()

    private val _orders = MutableStateFlow<UiState<OrdersResponse>>(UiState.Loading)
    val orders: StateFlow<UiState<OrdersResponse>> = _orders.asStateFlow()

    private val _orderType = MutableStateFlow("")
    val orderType: StateFlow<String> = _orderType.asStateFlow()

    private val _dateRange = MutableStateFlow(DateRange())
    val dateRange: StateFlow<DateRange> = _dateRange.asStateFlow()

    private val _orderCustomerId = MutableStateFlow(0)
    val orderCustomerId: StateFlow<Int> = _orderCustomerId.asStateFlow()

    private val _orderCustomerName = MutableStateFlow("")
    val orderCustomerName: StateFlow<String> = _orderCustomerName.asStateFlow()

    private val _documentDetail = MutableStateFlow<UiState<com.simpleerp.mobile.data.DocumentDetail>>(
        UiState.Loading
    )
    val documentDetail: StateFlow<UiState<com.simpleerp.mobile.data.DocumentDetail>> =
        _documentDetail.asStateFlow()

    /**
     * 单据页「换了筛选条件就回顶」的一次性标记。
     *
     * 和客户页排序回顶同一个道理（2026-10-23）：换了日期/类型/客户之后，
     * 停在原来的行号上毫无意义 —— 那是另一批数据。同样必须**等新数据上屏后**再滚，
     * 否则 LazyColumn 会按 item key 把旧位置拉回来。
     */
    private val _ordersScrollReset = MutableStateFlow(false)
    val ordersScrollReset: StateFlow<Boolean> = _ordersScrollReset.asStateFlow()

    fun consumeOrdersScrollReset() {
        _ordersScrollReset.value = false
    }

    private val _products = MutableStateFlow<UiState<ProductsResponse>>(UiState.Loading)
    val products: StateFlow<UiState<ProductsResponse>> = _products.asStateFlow()

    private val _productQuery = MutableStateFlow("")
    val productQuery: StateFlow<String> = _productQuery.asStateFlow()

    /**
     * 商品排序：false = 拼音（默认），true = 价格（最低价）。
     *
     * 用户 2026-10-22 要求「和客户页是一样的」：客户页是「拼音 / 欠款」两颗 chip，
     * 商品页对应成「拼音 / 价格」—— 商品没有欠款，店主查商品主要就是看价格。
     */
    private val _productSortPrice = MutableStateFlow(false)
    val productSortPrice: StateFlow<Boolean> = _productSortPrice.asStateFlow()

    /** 商品列表正倒序。默认正序：拼音 A→Z / 价格 便宜→贵。 */
    private val _productDescending = MutableStateFlow(false)
    val productDescending: StateFlow<Boolean> = _productDescending.asStateFlow()

    /** 商品页「换排序后回顶」的一次性标记，语义同 [customersScrollReset]。 */
    private val _productsScrollReset = MutableStateFlow(false)
    val productsScrollReset: StateFlow<Boolean> = _productsScrollReset.asStateFlow()

    fun consumeProductsScrollReset() {
        _productsScrollReset.value = false
    }

    private val _productDetail = MutableStateFlow<UiState<ProductDetailResponse>>(UiState.Loading)
    val productDetail: StateFlow<UiState<ProductDetailResponse>> = _productDetail.asStateFlow()

    private val _cacheNewest = MutableStateFlow(0L)
    val cacheNewest: StateFlow<Long> = _cacheNewest.asStateFlow()

    /** 搜索输入防抖：柜台前打字快，每键一请求会把局域网打满。 */
    private var searchJob: Job? = null

    // -----------------------------------------------------------------------
    // 启动
    // -----------------------------------------------------------------------

    init {
        viewModelScope.launch { bootstrap() }
    }

    suspend fun bootstrap() {
        // 地址一律以 DataStore 为准。
        //
        // 踩过的坑：曾经写成「内存值优先、空了才读 DataStore」。真机上配对成功后
        // Activity 会因为键盘/窗口变化重建，ViewModel 跟着重建，新实例内存地址是空的，
        // 于是 bootstrap 读到空串，界面被打回「连接电脑」—— 而设备其实已经配对成功。
        // DataStore 是唯一可靠的来源，内存值只用于「刚写入、还没落盘」的极短窗口。
        val stored = serverConfig.baseUrl()
        val url = stored.ifEmpty { _baseUrl.value }
        _baseUrl.value = url
        api.updateBaseUrl(url)
        if (url.isEmpty()) {
            _needsSetup.value = true
            _bootstrapped.value = true
            return
        }
        _needsSetup.value = false
        // 设置页标题用的公司名：先亮本地副本（断网也能显示上次连的是哪家店），
        // 联网探到 meta 后再用服务端的值覆盖并落盘。
        _shopName.value = serverConfig.shopName()
        // 先探一次 meta：地址对不对、服务活不活、要不要配对，一次问清。
        when (val result = api.get("/m/api/meta")) {
            is ApiResult.Success -> {
                val decoded = api.decode(result.body, MetaResponse.serializer())
                if (decoded == null) {
                    _needsSetup.value = true
                } else {
                    _meta.value = decoded
                    _needsPairing.value = !decoded.paired
                    if (decoded.shopName.isNotBlank()) {
                        _shopName.value = decoded.shopName
                        serverConfig.saveShopName(decoded.shopName)
                    }
                    if (!decoded.paired) {
                        // meta 是不过设备门的，所以这里拿到 paired=false 才是真的没配对。
                    }
                }
            }
            is ApiResult.Unauthorized -> _needsPairing.value = true
            is ApiResult.Unreachable -> {
                // 连不上：可能是电脑没开。允许进主界面看缓存，页面上会标注离线。
                _needsPairing.value = false
            }
            is ApiResult.Failure -> _needsPairing.value = false
            ApiResult.NoServerConfigured -> _needsSetup.value = true
        }
        _bootstrapped.value = true
        _cacheNewest.value = cache.newestTimestamp() ?: 0L
        if (!_needsPairing.value && !_needsSetup.value) {
            refreshAll()
        }
    }

    /** 设置页「重新检测」：重跑一次启动流程（地址/配对/元信息）。 */
    fun recheck() {
        viewModelScope.launch {
            _bootstrapped.value = false
            bootstrap()
        }
    }

    fun saveBaseUrl(raw: String, onDone: (String) -> Unit = {}) {
        viewModelScope.launch {
            serverConfig.saveBaseUrl(raw)
            val normalized = serverConfig.baseUrl()
            _baseUrl.value = normalized
            api.updateBaseUrl(normalized)
            // 换地址等于换电脑：旧 cookie 与新主机不匹配，必须清掉重新配对。
            // 用 api 内部那个实例，否则清的不是真正发请求时用的那份。
            api.cookieStore.clear()
            _needsSetup.value = normalized.isEmpty()
            _bootstrapped.value = false
            _meta.value = null
            // 换电脑就等于换店：旧店名不能留在标题上。
            _shopName.value = ""
            bootstrap()
            onDone(normalized)
        }
    }

    /**
     * 断开连接：清掉本机配对态，回到「连接电脑」页。
     *
     * **只清本机**：服务端的设备登记不动。这跟「撤销设备」是两件事 ——
     * 撤销是管理动作（只有电脑端能做），这里是本机的「我不用这台电脑了」。
     * 清掉之后要重新配对才能用，所以 UI 上必须确认一次，不能一点就走。
     *
     * 地址也一起清：留着一个连不上的地址，用户回到设置页会看到「未设置」更清楚，
     * 反正重新配对时扫码或手输都会再写进来。
     */
    fun disconnect() {
        viewModelScope.launch {
            api.cookieStore.clear()
            serverConfig.clear()
            _baseUrl.value = ""
            api.updateBaseUrl("")
            // 缓存**不清**：断开是「不再连这台电脑」，不是「忘掉看过的数据」。
            // 清了的话用户重新连回来要重新等一遍全量加载，没有道理。
            _meta.value = null
            // 公司名要清：断开的语义就是「不再连这台电脑」，留着会让下一次
            // 连到别的店时标题先闪一下旧店名（`ServerConfig.clear()` 已一并清盘）。
            _shopName.value = ""
            _needsPairing.value = false
            _needsSetup.value = true
            _bootstrapped.value = true
        }
    }

    fun setThemeMode(mode: ThemeMode) {
        _themeMode.value = mode
    }

    fun selectTab(next: Tab) {
        _tab.value = next
        when (next) {
            Tab.TODAY -> refreshToday()
            Tab.CUSTOMERS -> refreshCustomers()
            Tab.ORDERS -> refreshOrders()
            Tab.PRODUCTS -> refreshProducts()
            Tab.SETTINGS -> viewModelScope.launch {
                _cacheNewest.value = cache.newestTimestamp() ?: 0L
            }
        }
    }

    // -----------------------------------------------------------------------
    // 下拉刷新（用户手势）
    // -----------------------------------------------------------------------

    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing.asStateFlow()

    /**
     * 下拉刷新：只刷新**当前 tab**，并驱动刷新指示器。
     *
     * 2026-10-23 修一个真机必现的缺陷：这里原来调 `refreshToday()`，
     * 而 `refreshToday()` 自己只是 `viewModelScope.launch { ... }` 就返回了 ——
     * 于是本函数第一行置 true、紧接着 `finally` 立刻置 false，**网络请求根本还没发出**。
     * `PullToRefreshBox` 的指示器状态机被打断在半路：图标既不转、也不消失，
     * 要等切 tab 把整棵树重建才清掉（用户实测原话）。
     *
     * 现在改成**等待真正的加载完成**（`loadXxx()` 是 suspend，返回即结束），
     * 并加一个最短可见时长，避免局域网秒回时指示器一闪而过、用户以为没刷新。
     */
    fun pullRefresh(tab: Tab) {
        viewModelScope.launch {
            _refreshing.value = true
            val startedAt = System.currentTimeMillis()
            try {
                when (tab) {
                    Tab.TODAY -> loadToday()
                    Tab.CUSTOMERS -> loadCustomers()
                    Tab.ORDERS -> loadOrders()
                    Tab.PRODUCTS -> loadProducts()
                    Tab.SETTINGS -> Unit
                }
            } finally {
                // 至少让指示器转够 MIN_REFRESH_VISIBLE_MS，用户才看得出「刷过了」。
                val elapsed = System.currentTimeMillis() - startedAt
                if (elapsed < MIN_REFRESH_VISIBLE_MS) {
                    delay(MIN_REFRESH_VISIBLE_MS - elapsed)
                }
                _refreshing.value = false
            }
        }
    }

    fun refreshAll() {
        refreshToday()
        refreshCustomers()
        refreshOrders()
        refreshProducts()
    }

    // -----------------------------------------------------------------------
    // 配对
    // -----------------------------------------------------------------------

    fun submitPairCode(code: String, onResult: (String?) -> Unit) {
        viewModelScope.launch {
            // platform=app：让电脑端设备列表能区分「手机 App」和「浏览器」。
            // 不带这个字段的话，一个 App 和一个浏览器在设置页会显示成两条
            // 一模一样的「Android 手机」，撤销设备时没法判断该撤哪条。
            val body = """{"code":"$code","label":"手机 App","platform":"app"}"""
            when (val result = api.postJson("/m/api/pair", body)) {
                is ApiResult.Success -> {
                    _needsPairing.value = false
                    bootstrap()
                    onResult(null)
                }
                is ApiResult.Failure -> onResult(result.message)
                is ApiResult.Unreachable -> onResult("连不上电脑，请确认手机和电脑在同一个 WiFi。")
                ApiResult.Unauthorized -> onResult("配对被拒绝，请在电脑上重新生成配对码。")
                ApiResult.NoServerConfigured -> onResult("还没设置电脑地址。")
            }
        }
    }

    // -----------------------------------------------------------------------
    // 今日概览
    // -----------------------------------------------------------------------

    fun refreshToday() {
        viewModelScope.launch { loadToday() }
    }

    /** 真正干活的那一层（suspend）：下拉刷新要能等它结束，所以必须可挂起。 */
    private suspend fun loadToday() {
        // 只在「手上还没有数据」时才转圈。已有数据就保持显示，
        // 让 publish/网络结果直接替换 —— 否则每次切 tab 都会先闪一下骨架，
        // 有了缓存秒开反而看起来更卡。
        if (_today.value !is UiState.Ready) _today.value = UiState.Loading
        _today.value = load(
            cacheKey = "today",
            path = "/m/api/today",
            publish = { _today.value = it },
            serializer = { text ->
                api.decode(text, TodayResponse.serializer())?.today
            },
        )
    }

    // -----------------------------------------------------------------------
    // 客户
    // -----------------------------------------------------------------------

    fun onCustomerQueryChange(value: String) {
        _customerQuery.value = value
        searchJob?.cancel()
        searchJob = viewModelScope.launch {
            delay(220)
            refreshCustomers()
        }
    }

    fun toggleCustomerSort() {
        _customerSortDebt.value = !_customerSortDebt.value
        // 换排序方式时重置方向：拼音默认正序、欠款默认「多→少」。
        // 不重置的话，用户从「欠款倒序」切到「拼音」会看到 Z→A，很怪。
        _customerDescending.value = false
        refreshCustomers(resetScroll = true)
    }

    /** 正序/倒序切换。只反转方向，不换排序依据。 */
    fun toggleCustomerOrder() {
        _customerDescending.value = !_customerDescending.value
        refreshCustomers(resetScroll = true)
    }

    /**
     * 拉客户列表。[resetScroll] = 换排序/方向后要回顶。
     *
     * 回顶标记**必须在新数据发布之后**才置位：LazyColumn 换数据时会按 item key
     * 重新锚定位置，先滚到 0 再换数据，它会把旧顺序的第一行（现在排在几十行之后）
     * 拉回视线 —— 这正是「切换后不回到顶部，而是在原位置刷新」的成因。
     */
    fun refreshCustomers(resetScroll: Boolean = false) {
        viewModelScope.launch {
            loadCustomers()
            if (resetScroll) _customersScrollReset.value = true
        }
    }

    private suspend fun loadCustomers() {
        val query = buildMap {
            val q = _customerQuery.value.trim()
            if (q.isNotEmpty()) put("q", q)
            if (_customerSortDebt.value) put("sort", "debt")
            // 只在倒序时才传：正序是服务端的默认方向，多传一个 asc
            // 会让缓存键分叉，同一份数据存两遍。
            if (_customerDescending.value) put("order", "desc")
        }
        // 只在「手上还没有数据」时才转圈。已有数据就保持显示，
        // 让 publish/网络结果直接替换 —— 否则每次切 tab 都会先闪一下骨架，
        // 有了缓存秒开反而看起来更卡。
        if (_customers.value !is UiState.Ready) _customers.value = UiState.Loading
        _customers.value = load(
            publish = { _customers.value = it },
            cacheKey = "customers:${_customerQuery.value}:${_customerSortDebt.value}:${_customerDescending.value}",
            path = "/m/api/customers",
            query = query,
            serializer = { text ->
                api.decode(text, CustomersResponse.serializer())?.customers
            },
        )
    }

    fun openCustomer(customerId: Int) {
        viewModelScope.launch {
            _customerDetail.value = UiState.Loading
            _customerDetail.value = load(
                cacheKey = "customer:$customerId",
                path = "/m/api/customers/$customerId",
                serializer = { text ->
                    api.decode(text, CustomerDetailResponse.serializer())
                },
            )
        }
    }

    /** 从客户详情跳到该客户的单据列表（客户筛选是页面上下文，不是全局筛选）。 */
    fun showOrdersForCustomer(customerId: Int, customerName: String) {
        _orderCustomerId.value = customerId
        _orderCustomerName.value = customerName
        _tab.value = Tab.ORDERS
        refreshOrders(resetScroll = true)
    }

    fun clearOrderCustomer() {
        _orderCustomerId.value = 0
        _orderCustomerName.value = ""
        refreshOrders(resetScroll = true)
    }

    // -----------------------------------------------------------------------
    // 单据
    // -----------------------------------------------------------------------

    fun setOrderType(type: String) {
        _orderType.value = type
        refreshOrders(resetScroll = true)
    }

    fun setDateRange(range: DateRange) {
        _dateRange.value = range
        refreshOrders(resetScroll = true)
    }

    /** 拉单据列表。[resetScroll] = 换了筛选条件后要回顶（见 [ordersScrollReset]）。 */
    fun refreshOrders(resetScroll: Boolean = false) {
        viewModelScope.launch {
            loadOrders()
            if (resetScroll) _ordersScrollReset.value = true
        }
    }

    private suspend fun loadOrders() {
        val range = _dateRange.value
        val query = buildMap {
            put("date_mode", range.mode.ifEmpty { "day" })
            if (range.startDate.isNotEmpty()) put("start_date", range.startDate)
            if (range.endDate.isNotEmpty()) put("end_date", range.endDate)
            if (range.mode == "year") put("year", range.year.toString())
            if (range.mode == "month") {
                put("year", range.year.toString())
                put("month", range.month.toString())
            }
            val type = _orderType.value
            if (type.isNotEmpty()) put("order_type", type)
            val customer = _orderCustomerId.value
            if (customer > 0) put("customer_id", customer.toString())
        }
        // 只在「手上还没有数据」时才转圈。已有数据就保持显示，
        // 让 publish/网络结果直接替换 —— 否则每次切 tab 都会先闪一下骨架，
        // 有了缓存秒开反而看起来更卡。
        if (_orders.value !is UiState.Ready) _orders.value = UiState.Loading
        _orders.value = load(
            publish = { _orders.value = it },
            cacheKey = "orders:${query.toSortedMap()}",
            path = "/m/api/orders",
            query = query,
            serializer = { text -> api.decode(text, OrdersResponse.serializer()) },
        )
    }

    fun openDocument(orderType: String, documentId: Int) {
        viewModelScope.launch {
            _documentDetail.value = UiState.Loading
            val state = load(
                cacheKey = "document:$orderType:$documentId",
                path = "/m/api/orders/$orderType/$documentId",
                serializer = { text ->
                    api.decode(text, DocumentDetailResponse.serializer())?.document
                },
            )
            // 断网且这张单据从没联网打开过 → 没有缓存 → 原来会显示「连不上电脑」，
            // 用户看到的是「明明列表里有这张单，点进去却说连不上」。
            //
            // 这里用**列表里已有的摘要**降级渲染：单号、日期、客户、金额、类型
            // 全都在摘要里，只缺明细行。比一个错误页有用得多 —— 店主要核对金额、
            // 报单号，这两件事摘要就够。
            //
            // 只在「确实连不上」时降级；服务端明确报错（比如单据被删了）仍然显示错误，
            // 否则用户会以为单据还在。
            _documentDetail.value = if (state is UiState.Failed) {
                fallbackDocumentDetail(orderType, documentId) ?: state
            } else {
                state
            }
        }
    }

    /**
     * 从已加载的列表里捞一张单据的摘要，拼成一份「离线简版」详情。
     *
     * 数据来源按优先级：当前单据列表 → 各客户详情里的「最近单据」。
     * 后者很重要：店主常常是「客户详情 → 点最近单据」进来的，那条路径上
     * 单据列表根本没加载过，但客户详情里就有摘要。
     *
     * 找不到返回 null（调用方保持原来的错误态）。
     */
    private fun fallbackDocumentDetail(orderType: String, documentId: Int): UiState<DocumentDetail>? {
        val summary = findDocumentSummary(orderType, documentId) ?: return null
        val detail = DocumentDetail(
            id = summary.id,
            orderNo = summary.orderNo,
            orderType = summary.orderType,
            typeLabel = summary.typeLabel,
            customerName = summary.customerName,
            businessDate = summary.businessDate,
            dateLabel = summary.dateLabel,
            totalCents = summary.amountCents,
            totalText = summary.amountText,
            // items 留空：摘要里没有明细。页面会显示「明细需要联网查看」，
            // 而不是假装这单没有商品。
            items = emptyList(),
        )
        return UiState.Ready(detail, fromCache = true, fetchedAt = _cacheNewest.value)
    }

    /** 在所有已加载的数据里找一张单据摘要。 */
    private fun findDocumentSummary(orderType: String, documentId: Int): DocumentSummary? {
        (_orders.value as? UiState.Ready)?.value?.documents?.forEach { doc ->
            if (doc.id == documentId && doc.orderType == orderType) return doc
        }
        (_customerDetail.value as? UiState.Ready)?.value?.documents?.forEach { doc ->
            if (doc.id == documentId && doc.orderType == orderType) return doc
        }
        return null
    }

    // -----------------------------------------------------------------------
    // 商品价
    // -----------------------------------------------------------------------

    fun onProductQueryChange(value: String) {
        _productQuery.value = value
        searchJob?.cancel()
        searchJob = viewModelScope.launch {
            delay(220)
            refreshProducts()
        }
    }

    fun refreshProducts(resetScroll: Boolean = false) {
        viewModelScope.launch {
            loadProducts()
            if (resetScroll) _productsScrollReset.value = true
        }
    }

    private suspend fun loadProducts() {
        val q = _productQuery.value.trim()
        val sort = if (_productSortPrice.value) "price" else "pinyin"
        // 只在「手上还没有数据」时才转圈。已有数据就保持显示，
        // 让 publish/网络结果直接替换 —— 否则每次切 tab 都会先闪一下骨架，
        // 有了缓存秒开反而看起来更卡。
        if (_products.value !is UiState.Ready) _products.value = UiState.Loading
        // 排序条件必须进缓存键：同一句搜索在「拼音」和「价格」下是两份不同的
        // 列表，共用键会让离线时看到另一个排序的结果（看起来像排序没生效）。
        val cacheKey = "products:$sort:${if (_productDescending.value) "desc" else "asc"}:$q"
        val params = buildMap {
            if (q.isNotEmpty()) put("q", q)
            put("sort", sort)
            // 只在倒序时才传：正序是服务端默认，多传一个 asc 会让缓存键分叉。
            if (_productDescending.value) put("order", "desc")
        }
        _products.value = load(
            publish = { _products.value = it },
            cacheKey = cacheKey,
            path = "/m/api/products",
            query = params,
            serializer = { text -> api.decode(text, ProductsResponse.serializer()) },
        )
    }

    /** 换排序依据：拼音 ↔ 价格。换档时重置方向（拼音默认 A→Z、价格默认便宜→贵）。 */
    fun toggleProductSort() {
        _productSortPrice.value = !_productSortPrice.value
        // 不重置的话，用户从「价格倒序」切到「拼音」会看到 Z→A，很怪。
        _productDescending.value = false
        refreshProducts(resetScroll = true)
    }

    /** 正序/倒序切换。只反转方向，不换排序依据。 */
    fun toggleProductOrder() {
        _productDescending.value = !_productDescending.value
        refreshProducts(resetScroll = true)
    }

    fun openProduct(productId: Int) {
        viewModelScope.launch {
            _productDetail.value = UiState.Loading
            _productDetail.value = load(
                cacheKey = "product:$productId",
                path = "/m/api/products/$productId",
                serializer = { text -> api.decode(text, ProductDetailResponse.serializer()) },
            )
        }
    }

    // -----------------------------------------------------------------------
    // 内部：统一把 LoadOutcome 映射成 UiState
    // -----------------------------------------------------------------------

    /**
     * 先给缓存（如果有），再走网络。**顺序就是体验**。
     *
     * 原来的实现是「先网络、超时才回落缓存」，所以电脑关机时切页面要等满
     * connectTimeout 才看到数据（2026-10-22 实测 8.7 秒白屏转圈，用户报
     * 「每个页面读取缓存的时间有点过长」）。用户要的是「打开就有数」，
     * 而不是「等一会儿证明连不上，再给旧数」。
     *
     * 现在：命中缓存 → 立刻 Ready（界面瞬间出数、带离线横幅）；
     * 同时网络请求照常发，成功且内容有变时再刷新成在线数据。
     * 没缓存时行为不变（Loading → 结果），所以首次使用不会出现空闪。
     */
    private suspend fun <T> load(
        cacheKey: String,
        path: String,
        query: Map<String, String> = emptyMap(),
        publish: ((UiState<T>) -> Unit)? = null,
        serializer: (String) -> T?,
    ): UiState<T> {
        // 1) 有缓存就先亮出来，让用户马上看到上次的数。
        //    `publish` 是「立刻上屏」的通道：调用方把它接到自己的 StateFlow 上，
        //    这样缓存值在第一个挂起点之前就渲染了，用户感觉是「秒开」。
        //    网络请求继续往下走，成功了会用新鲜数据覆盖它。
        if (publish != null) {
            val cached = cache.get(cacheKey)
            val cachedValue = cached?.let { serializer(it.body) }
            if (cached != null && cachedValue != null) {
                _cacheNewest.value = maxOf(_cacheNewest.value, cached.fetchedAt)
                publish(UiState.Ready(cachedValue, fromCache = true, fetchedAt = cached.fetchedAt))
            }
        }
        // 显式写 <T>：`serializer` 的类型是 (String) -> T?，不指定的话编译器会把
        // loadWithCache 的 T 推成 T?，得到 Loaded<T?> 而无法赋给 UiState<T>。
        return when (val outcome = api.loadWithCache<T>(cache, cacheKey, path, query, serializer)) {
            is LoadOutcome.Ok -> {
                val data: Loaded<T> = outcome.data
                if (data.fromCache) {
                    _cacheNewest.value = maxOf(_cacheNewest.value, data.fetchedAt)
                }
                UiState.Ready(data.value, data.fromCache, data.fetchedAt)
            }
            is LoadOutcome.Error -> UiState.Failed(outcome.message)
            LoadOutcome.NeedsPairing -> {
                _needsPairing.value = true
                UiState.NeedsPairing
            }
            LoadOutcome.NeedsSetup -> {
                _needsSetup.value = true
                UiState.NeedsSetup
            }
        }
    }
}
