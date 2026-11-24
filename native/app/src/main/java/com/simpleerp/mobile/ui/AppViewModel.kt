package com.simpleerp.mobile.ui

import android.app.Application
import android.net.Uri
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
import com.simpleerp.mobile.data.SnapshotResponse
import com.simpleerp.mobile.data.TodayOverview
import com.simpleerp.mobile.data.TodayResponse
import com.simpleerp.mobile.net.ApiClient
import com.simpleerp.mobile.net.ApiResult
import com.simpleerp.mobile.net.CookiePersistence
import com.simpleerp.mobile.net.Loaded
import com.simpleerp.mobile.net.LoadOutcome
import com.simpleerp.mobile.net.MetaProbe
import com.simpleerp.mobile.net.PersistentCookieStore
import com.simpleerp.mobile.BuildConfig
import com.simpleerp.mobile.net.PeerPack
import com.simpleerp.mobile.net.ResponseCache
import com.simpleerp.mobile.net.ServerConfig
import com.simpleerp.mobile.net.SnapshotStore
import com.simpleerp.mobile.net.StartupRoute
import com.simpleerp.mobile.net.StartupRouting
import com.simpleerp.mobile.net.loadWithCache
import com.simpleerp.mobile.ui.theme.ThemeMode
import com.simpleerp.mobile.ui.util.OfflineOrderFilter
import com.simpleerp.mobile.ui.util.formatCacheTime
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.serializer
import java.io.File

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
    private val snapshot = SnapshotStore(app)
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

    /**
     * 「连不上电脑」页（2026-10-31）。
     *
     * 只在「本地没确认过配对 + 探测不到电脑」时为 `true`。区分它和 [needsPairing] 很重要：
     * 配对页意味着「电脑在，但这台手机没被允许」；这里是「手机压根没连上电脑」——
     * 处置方式完全不同（前者输码，后者查 WiFi/地址/防火墙）。
     */
    private val _connectionLost = MutableStateFlow(false)
    val connectionLost: StateFlow<Boolean> = _connectionLost.asStateFlow()

    /** 「连不上电脑」页上那句原因（地址 / HTTP 状态码），给人看的，不参与判断。 */
    private val _connectionDetail = MutableStateFlow("")
    val connectionDetail: StateFlow<String> = _connectionDetail.asStateFlow()

    /**
     * 用户明知没配对、仍选择了「先看离线缓存」（A2）。
     *
     * 为什么必须有这个标记：配对页上那颗次级入口如果只是「关掉配对页」，
     * 接下来每个请求都 401，`load()` 立刻把 `_needsPairing` 翻回 true ——
     * 用户点了按钮、又回到配对页，等于没点。
     *
     * 两个清掉它的时机，都是用户明确表示「我要重新连」：
     * 「重新检测」（[recheck]）、提交新地址（[saveBaseUrl]）、断开连接（[disconnect]）。
     * 不进 StateFlow：没有任何界面直接渲染它，只影响 401 的处置方式。
     */
    private var offlineBrowse = false

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

    /**
     * 商品详情页（某商品 → 每个客户的专属价）的**页内筛选**。
     *
     * 2026-10-24 用户要求（第七张截图）：商品名下面加「搜索框 + 拼音/价格 + 正倒序箭头」，
     * 箭头点击要「回顶刷新」。三件事都是页内状态，不属于商品列表 ——
     * 混用会让「在列表搜了 A、点进详情却在搜 B」，所以单独一组。
     *
     * 为什么要放在 ViewModel 而不是 Composable 的 `remember`：
     * 从详情页进客户详情再返回时，Composable 会重建，`remember` 的筛选条件就丢了，
     * 用户得重新输一遍客户名。
     */
    private val _productDetailQuery = MutableStateFlow("")
    val productDetailQuery: StateFlow<String> = _productDetailQuery.asStateFlow()

    /** 详情页排序：false = 价格（默认，贵的在前），true = 客户拼音。 */
    private val _productDetailSortPinyin = MutableStateFlow(false)
    val productDetailSortPinyin: StateFlow<Boolean> = _productDetailSortPinyin.asStateFlow()

    /** 详情页正倒序。默认 true = 倒序（价格贵的在前），与网页版默认一致。 */
    private val _productDetailDescending = MutableStateFlow(true)
    val productDetailDescending: StateFlow<Boolean> = _productDetailDescending.asStateFlow()

    /** 详情页「换排序/方向后回顶」的一次性标记，语义同 [customersScrollReset]。 */
    private val _productDetailScrollReset = MutableStateFlow(false)
    val productDetailScrollReset: StateFlow<Boolean> = _productDetailScrollReset.asStateFlow()

    fun consumeProductDetailScrollReset() {
        _productDetailScrollReset.value = false
    }

    private val _cacheNewest = MutableStateFlow(0L)
    val cacheNewest: StateFlow<Long> = _cacheNewest.asStateFlow()

    /**
     * 「立即同步」的状态（2026-10-24）。
     *
     * 为什么要一个专门的状态机，而不是复用 `refreshing`：同步要跑十几到几十个
     * 请求（列表 + 每张单据详情），界面必须能显示「正在同步 / 成功，缓存了 N 项 /
     * 失败原因」。用下拉刷新的转圈表示「同步好了没」太含糊 —— 店主关电脑前
     * 正是要一个明确的答案。
     */
    sealed class SyncState {
        /** 还没同步过（本次进程内）。 */
        data object Idle : SyncState()
        data object Running : SyncState()
        data class Done(val entryCount: Int, val atMillis: Long) : SyncState()
        data class Failed(val message: String) : SyncState()
    }

    private val _syncState = MutableStateFlow<SyncState>(SyncState.Idle)
    val syncState: StateFlow<SyncState> = _syncState.asStateFlow()

    /** 上次同步的时间（epoch 毫秒，0 = 从未）。设置页「离线缓存」那张卡用它。 */
    private val _lastSyncAt = MutableStateFlow(0L)
    val lastSyncAt: StateFlow<Long> = _lastSyncAt.asStateFlow()

    /**
     * 同步回溯几个月（含本月，默认 2）。设置页可选「近 2 / 3 / 6 / 12 个月」。
     * 2026-11-01 用户要求「默认两个月，也能自己选更长」—— 它直接决定离线能翻多远。
     */
    private val _syncMonths = MutableStateFlow(ServerConfig.DEFAULT_SYNC_MONTHS)
    val syncMonths: StateFlow<Int> = _syncMonths.asStateFlow()

    // ---- 同伴数据包（2026-11-21）----

    /** 当前快照是不是从同伴手机导入的：导入时刻（epoch 毫秒），0 = 不是。 */
    private val _peerImportedAt = MutableStateFlow(0L)
    val peerImportedAt: StateFlow<Long> = _peerImportedAt.asStateFlow()

    /** 同伴数据包里记录的店名。 */
    private val _peerSourceShop = MutableStateFlow("")
    val peerSourceShop: StateFlow<String> = _peerSourceShop.asStateFlow()

    /** 导入/导出正在进行中（按钮防连点 + 显示"处理中"）。 */
    private val _peerBusy = MutableStateFlow(false)
    val peerBusy: StateFlow<Boolean> = _peerBusy.asStateFlow()

    /** 一次性的结果提示（成功/失败都说清楚）。UI 消费后调用 [consumePeerMessage]。 */
    private val _peerMessage = MutableStateFlow<String?>(null)
    val peerMessage: StateFlow<String?> = _peerMessage.asStateFlow()

    /** 已生成、待交给系统分享面板的文件。UI 消费后调用 [consumeShareFile]。 */
    private val _shareFile = MutableStateFlow<File?>(null)
    val shareFile: StateFlow<File?> = _shareFile.asStateFlow()

    fun consumePeerMessage() {
        _peerMessage.value = null
    }

    /** UI 层遇到无法继续的情况（如分享面板起不来）时，用它把原因说给用户听。 */
    fun reportPeerError(message: String) {
        _peerMessage.value = message
    }

    fun consumeShareFile() {
        _shareFile.value = null
    }

    /** 搜索输入防抖：柜台前打字快，每键一请求会把局域网打满。 */
    private var searchJob: Job? = null

    /** 同步串行化：两次同步重叠会互相覆盖快照文件，也白费流量。 */
    private var syncJob: Job? = null

    // -----------------------------------------------------------------------
    // 启动
    // -----------------------------------------------------------------------

    init {
        viewModelScope.launch { bootstrap() }
    }

    /**
     * 「谁是最新的一次启动探测」（A10）。
     *
     * 四个入口都会起 `bootstrap()`（init / recheck / saveBaseUrl / 配对成功），而它中间
     * 要等一次最长 1.5 秒的 `meta` 探测。用户在这段时间里改了地址、或连点了「重新检测」，
     * 旧的那次探测随后返回，会把**新地址/新状态**覆盖成旧结论 ——
     * 表现是「地址明明改了，界面还停在上一个地址的页面」。
     *
     * 为什么用代号计数而不是取消 Job：`bootstrap()` 是 suspend，且在 `saveBaseUrl`
     * 与 `submitPairCode` 里被 await（它们的收尾逻辑在它之后）。取消那个 Job 会把
     * 调用方的协程一起取消（同一个 viewModelScope 父作用域），配对成功的收尾就没了。
     * 代号计数不动协程树，只让过期的那次在下一个挂起点静默退场。
     */
    private var bootstrapGeneration = 0

    /** 代号是否还有效（见 [bootstrapGeneration]）。 */
    private fun isCurrentBootstrap(generation: Int): Boolean = generation == bootstrapGeneration

    suspend fun bootstrap() {
        val generation = ++bootstrapGeneration
        // 地址一律以 DataStore 为准。
        //
        // 踩过的坑：曾经写成「内存值优先、空了才读 DataStore」。真机上配对成功后
        // Activity 会因为键盘/窗口变化重建，ViewModel 跟着重建，新实例内存地址是空的，
        // 于是 bootstrap 读到空串，界面被打回「连接电脑」—— 而设备其实已经配对成功。
        // DataStore 是唯一可靠的来源，内存值只用于「刚写入、还没落盘」的极短窗口。
        val stored = serverConfig.baseUrl()
        // 读出地址这个挂起点里，可能有更新的一次 bootstrap 已经跑完并写入了新地址。
        // 这里必须先确认自己还是最新的一次，否则会用「旧地址」覆盖掉新的（A10）。
        if (!isCurrentBootstrap(generation)) return
        val url = stored.ifEmpty { _baseUrl.value }
        _baseUrl.value = url
        api.updateBaseUrl(url)
        if (url.isEmpty()) {
            // 2026-11-21：没有电脑地址，但本地已有快照（可能刚从同伴手机导入）时，
            // 不该把人锁在「连接电脑」页 —— 数据就在手机里，看得到比填地址更急。
            val cachedAt = snapshot.syncedAt()
            if (cachedAt > 0L) {
                // 这里已经过了一次挂起点：可能有人在这期间又启动了新的 bootstrap（A10）。
                if (!isCurrentBootstrap(generation)) return
                _needsSetup.value = false
                _needsPairing.value = false
                _connectionLost.value = false
                _cacheNewest.value = maxOf(_cacheNewest.value, cachedAt)
                _lastSyncAt.value = serverConfig.lastSyncAt()
                _syncMonths.value = serverConfig.syncMonths()
                _peerImportedAt.value = serverConfig.peerImportedAt()
                _peerSourceShop.value = serverConfig.peerSourceShop()
                _bootstrapped.value = true
                refreshAll()
                return
            }
            if (!isCurrentBootstrap(generation)) return
            _needsSetup.value = true
            _bootstrapped.value = true
            return
        }
        // 设置页标题用的公司名：先亮本地副本（断网也能显示上次连的是哪家店），
        // 联网探到 meta 后再用服务端的值覆盖并落盘。
        val storedShopName = serverConfig.shopName()
        // 本地是否**确认过**配对：决定「连不上」时是进主界面看缓存，还是停在连不上页。
        val pairedConfirmed = serverConfig.pairedConfirmed()
        // 同伴导入的时刻要在探测之前读：探测结论是 401 时，路由要不要按「离线」放行
        // 就靠它（A3）。本机自己同步成功一次后这个标记会被清掉，行为自动恢复。
        val peerImportedAt = serverConfig.peerImportedAt()
        // 上面三个读取也都是挂起点：先确认自己还是最新的一次，再开始写状态（A10）。
        if (!isCurrentBootstrap(generation)) return
        _needsSetup.value = false
        _shopName.value = storedShopName
        var probe = MetaProbe.UNREACHABLE
        var detail = ""
        // 探测到的 meta 先放局部变量，等确认「这次是最新的一次」之后才写状态（A10）。
        var probedMeta: MetaResponse? = null
        // 先探一次 meta：地址对不对、服务活不活、要不要配对，一次问清。
        when (val result = api.get("/m/api/meta")) {
            is ApiResult.Success -> {
                val decoded = api.decode(result.body, MetaResponse.serializer())
                if (decoded == null) {
                    // 地址通了但对方不是简单ERP（最常见：IP 被别的设备拿走了）。
                    probe = MetaProbe.SERVER_ERROR
                    detail = "这个地址回的内容看不懂，确认是不是这台电脑的地址。"
                } else {
                    probedMeta = decoded
                    probe = if (decoded.paired) MetaProbe.PAIRED else MetaProbe.NOT_PAIRED
                }
            }
            is ApiResult.Unauthorized -> probe = MetaProbe.NOT_PAIRED
            is ApiResult.Unreachable -> {
                probe = MetaProbe.UNREACHABLE
                detail = "连不上 $url。"
            }
            is ApiResult.Failure -> {
                probe = MetaProbe.SERVER_ERROR
                detail = "电脑回了 HTTP ${result.code}。"
            }
            ApiResult.NoServerConfigured -> {
                if (!isCurrentBootstrap(generation)) return
                _needsSetup.value = true
                _bootstrapped.value = true
                return
            }
        }
        // 探测是这里唯一的长挂起点（最长 1.5 秒）。之后全是状态写入，
        // 先确认自己还是最新的一次，否则旧结论会盖掉新地址/新状态（A10）。
        if (!isCurrentBootstrap(generation)) return
        probedMeta?.let { decoded ->
            _meta.value = decoded
            if (decoded.shopName.isNotBlank()) {
                _shopName.value = decoded.shopName
                serverConfig.saveShopName(decoded.shopName)
            }
        }

        // 配对态落盘：**只有服务端确认过才算**（见 ServerConfig.pairedConfirmed 的说明）。
        when (probe) {
            MetaProbe.PAIRED -> serverConfig.savePairedConfirmed(true)
            // NOT_PAIRED **不再**把本地的「确认过配对」抹成 false（A9）。
            //
            // 原来这里写 false：一次瞬时 401（服务端重启、会话表被清、代理插了一手）
            // 就把持久化标志抹掉，之后电脑真的关机时用户会被送去「连不上电脑」页，
            // 而不是照旧看缓存 —— 而「关电脑前同步一次、之后断网也能看」是明确需求。
            //
            // 取舍说清楚：如果设备**真的**被电脑端撤销了，本机仍会按「配过对」进主界面
            // 看缓存（每次请求都是 401，页面显示离线数据或错误）。这是可接受的 ——
            // 离线看得到旧数据，比把人挡在一个只说「连不上」的页面上更符合柜台场景；
            // 需要真正清掉这个标志的只有两个动作：用户点「断开连接」、换到另一台电脑（A4）。
            else -> Unit
        }

        // 同伴导入的数据在手机上放着，与服务端认不认这台设备无关（A3）：
        // 这时 401 不该被顶成全屏配对页，否则刚导入的东西一眼都看不到。
        val offlineAllowed = _peerImportedAt.value > 0L || peerImportedAt > 0L || offlineBrowse
        when (StartupRouting.route(probe, pairedConfirmed, offlineAllowed)) {
            StartupRoute.MAIN -> {
                _needsPairing.value = false
                _connectionLost.value = false
            }
            StartupRoute.PAIR -> {
                _needsPairing.value = true
                _connectionLost.value = false
            }
            StartupRoute.CONNECTION_LOST -> {
                // 从来没确认过配对、又连不上：停在「连不上电脑」页。
                // 不静默进主界面 —— 2026-10-31 就是这条把「防火墙拦了」演成了「已经配好」。
                _needsPairing.value = false
                _connectionLost.value = true
                _connectionDetail.value = detail
            }
        }
        _bootstrapped.value = true
        // 快照时间也要算进来：只同步过快照、还没逐页打开过时，`ResponseCache` 是空的，
        // 只取它的时间戳会显示成「还没有缓存」—— 明明数据就在。
        _cacheNewest.value = maxOf(cache.newestTimestamp() ?: 0L, snapshot.syncedAt())
        _lastSyncAt.value = serverConfig.lastSyncAt()
        _syncMonths.value = serverConfig.syncMonths()
        _peerImportedAt.value = serverConfig.peerImportedAt()
        _peerSourceShop.value = serverConfig.peerSourceShop()
        if (!_needsPairing.value && !_needsSetup.value && !_connectionLost.value) {
            refreshAll()
            // 打开 App 时顺手同步一次：这样「早上开店打开手机」看到的就不是
            // 昨晚关电脑前的旧数据。只在「距上次同步超过 10 分钟」时才真的发 ——
            // 柜台前反复切到后台再回来，不该每次都跑几十个请求。
            maybeAutoSync()
        }
    }

    /**
     * 「连不上电脑」页的次级入口：明知连不上，仍要进去看上次同步的缓存。
     *
     * 为什么保留这个入口：关电脑前同步一次、之后断网也能看，是明确的产品能力
     * （见 `docs/需求/2026-10-17_安卓只读端架构决策.md`）。新页面只是把
     * 「没连上」这件事说清楚，**不是**把离线看数据这条路堵死。
     */
    fun showOfflineCache() {
        // 配对页上也有这颗次级入口（A2）。从那里进来时 `_needsPairing` 还是 true，
        // 不一起关掉的话页面根本不会离开配对页；而只关掉又会被下一个 401 翻回来，
        // 所以还要记下「用户主动选了离线看」（见 offlineBrowse）。
        offlineBrowse = true
        _connectionLost.value = false
        _needsPairing.value = false
        refreshAll()
    }

    /**
     * 401 此刻该不该把人顶到配对页 —— 纯逻辑在
     * [StartupRouting.requiresPairingOnUnauthorized]，这里只负责喂数据。
     */
    private fun requiresPairingOnUnauthorized(): Boolean =
        StartupRouting.requiresPairingOnUnauthorized(
            peerImportedAt = _peerImportedAt.value,
            userChoseOfflineCache = offlineBrowse,
        )

    // -----------------------------------------------------------------------
    // 全量同步（2026-10-24：关电脑前让手机拉一遍）
    // -----------------------------------------------------------------------

    /** 距上次同步超过这么久才自动同步（毫秒）。10 分钟：够短到「打开就是新的」，
     *  够长到「切个后台回来不重跑」。 */
    private val autoSyncIntervalMs = 10 * 60 * 1000L

    /**
     * 回到前台 / 启动时调用：距上次同步超过 [autoSyncIntervalMs] 才同步。
     *
     * 为什么不每次都同步：一次同步是几十个请求的聚合，局域网也要一两秒；
     * 用户在「客户详情 → 返回 → 再进详情」之间来回切时，它不该反复跑。
     */
    fun maybeAutoSync() {
        viewModelScope.launch {
            // 同伴导入状态不自动同步（A3）。
            //
            // 为什么：`lastSyncAt` 记的是**同伴那份数据的截止时间**，通常很旧，
            // 于是这条节流必然放行 → 立刻跑一次整份同步 → B 从没配对过 → 全 401。
            // 用户视角是「刚导入完就闪一条红字/被顶去配对页」，把「导入成功」这件事盖掉。
            // 这个标记在本机自己同步成功一次后就被清掉，之后自动同步照旧。
            if (serverConfig.peerImportedAt() > 0L) return@launch
            val last = serverConfig.lastSyncAt()
            _lastSyncAt.value = last
            if (last > 0 && System.currentTimeMillis() - last < autoSyncIntervalMs) return@launch
            if (_needsPairing.value || _needsSetup.value || _connectionLost.value) return@launch
            runSync(silent = true)
        }
    }

    /** 回到前台刷当前页的最小间隔（毫秒）。旋转屏幕、输入法弹出都会连着触发 ON_RESUME。 */
    private val foregroundRefreshGapMs = 5_000L
    private var lastForegroundAt = 0L

    /**
     * 回到前台（`ON_RESUME`）：先刷**当前页**，再按 10 分钟节流跑全量同步（2026-10-31）。
     *
     * 为什么不能只靠 [maybeAutoSync]：它 10 分钟才跑一次快照，而用户切出去回个消息
     * 再回来，期望的是「刚才开的那张单已经在了」。所以这里先刷当前 tab —— 一个请求，
     * 局域网几十毫秒；快照是全量（放大后约 1~3 MB），仍按 10 分钟节流，频繁跑没有意义。
     *
     * 冷启动不用管：`bootstrap()` 末尾已经 `refreshAll()` + `maybeAutoSync()`。
     */
    fun onAppForeground() {
        // 冷启动时 ON_RESUME 会和 `init` 里的 bootstrap 撞车：那时 baseUrl 还没读出来，
        // 发出去的请求只会得到 NoServerConfigured，把界面短暂打回「连接电脑」页。
        // bootstrap 结束时自己会 refreshAll()，所以这里直接跳过更安全。
        if (!_bootstrapped.value) return
        if (_needsPairing.value || _needsSetup.value || _connectionLost.value) return
        viewModelScope.launch {
            val now = System.currentTimeMillis()
            if (now - lastForegroundAt < foregroundRefreshGapMs) return@launch
            lastForegroundAt = now
            when (_tab.value) {
                Tab.TODAY -> loadToday()
                Tab.CUSTOMERS -> loadCustomers()
                Tab.ORDERS -> loadOrders()
                Tab.PRODUCTS -> loadProducts()
                // 设置页没有列表数据要刷（公司名/版本来自 meta，重新检测是用户显式动作）。
                Tab.SETTINGS -> Unit
            }
            maybeAutoSync()
        }
    }

    /**
     * 「立即同步」：设置页那颗按钮。
     *
     * 成功时把整份快照落盘（列表 + 每张单据详情 + 客户/商品详情），
     * 于是断网时二级页面也能打开 —— 这是用户报的「缓存刷新不出来」的修法。
     */
    fun syncNow() {
        viewModelScope.launch { runSync(silent = false) }
    }

    /**
     * 设置页选「同步回溯范围」（近 2 / 3 / 6 / 12 个月）。
     *
     * 改完立刻同步一次：否则用户以为「选了近半年」已经生效，实际还是上次那份两个月缓存，
     * 断网后仍然翻不到更早的单，又变成一次「说了改了却没改」的误会。
     */
    fun setSyncMonths(months: Int) {
        val clamped = months.coerceIn(1, 24)
        if (clamped == _syncMonths.value) return
        viewModelScope.launch {
            serverConfig.saveSyncMonths(clamped)
            _syncMonths.value = clamped
            runSync(silent = false)
        }
    }

    private suspend fun runSync(silent: Boolean) {
        if (_needsPairing.value || _needsSetup.value || _connectionLost.value) {
            if (!silent) _syncState.value = SyncState.Failed("还没连上电脑，先在设置里配对。")
            return
        }
        // 串行化：正在跑就复用同一个任务，不叠加。
        if (syncJob?.isActive == true) return
        val job = viewModelScope.launch {
            _syncState.value = SyncState.Running
            // 同步范围由用户在设置页决定（默认 2 个月）；服务端据此生成覆盖区间的备份列表。
            val months = serverConfig.syncMonths()
            _syncMonths.value = months
            when (val result = api.get("/m/api/snapshot", mapOf("months" to months.toString()))) {
                is ApiResult.Success -> {
                    val decoded = api.decode(result.body, SnapshotResponse.serializer())
                    if (decoded == null || !decoded.ok) {
                        _syncState.value = SyncState.Failed("手机和电脑的版本对不上，请更新电脑端。")
                        return@launch
                    }
                    val at = System.currentTimeMillis()
                    snapshot.save(at, decoded.entries)
                    serverConfig.saveLastSyncAt(at)
                    // 记下本次覆盖的区间与备份列表键，断网本地筛选要用。
                    serverConfig.saveSyncSpan(decoded.syncFrom, decoded.syncTo, decoded.backupKey)
                    // 2026-11-21：这次是自己从电脑拉的，数据不再是"同伴的"。
                    // 不清的话，界面会一直标着「数据来自同伴」，而它其实已经是本机同步的了。
                    serverConfig.clearPeerImported()
                    _peerImportedAt.value = 0L
                    _peerSourceShop.value = ""
                    _lastSyncAt.value = at
                    _syncState.value = SyncState.Done(decoded.entryCount, at)
                    // 同步完刷新当前可见的数据：否则屏幕上还是同步前那一份，
                    // 而「已同步」标签已经是新的 —— 两处时间对不上会让人怀疑没同步成功。
                    refreshAll()
                }
                is ApiResult.Unreachable -> {
                    if (!silent) {
                        _syncState.value = SyncState.Failed("连不上电脑，请先开电脑、连同一个 WiFi。")
                    } else {
                        _syncState.value = SyncState.Idle
                    }
                }
                is ApiResult.Failure -> _syncState.value = SyncState.Failed(result.message)
                ApiResult.Unauthorized -> {
                    // 「按离线用」时 401 不翻转 `_needsPairing`（A3）：同伴导入或用户选了看缓存时，
                    // 401 是常态（B 从没配对过），把人顶去配对页会把刚导入的数据抢走。
                    // 这时按「连不上」处理：静默同步就什么都不显示，用户主动同步才说一句实话。
                    if (requiresPairingOnUnauthorized()) {
                        _needsPairing.value = true
                        // 用户主动点了「立即同步」却既没同步成功、又没被顶走时，必须说明原因 ——
                        // 否则界面上「正在同步…」直接没了，看起来像按钮没反应。
                        _syncState.value = SyncState.Failed("这台手机还没和电脑配对，现在只能看手机里的数据。")
                    } else if (!silent) {
                        _syncState.value = SyncState.Failed("连不上电脑，请先开电脑、连同一个 WiFi。")
                    } else {
                        _syncState.value = SyncState.Idle
                    }
                }
                ApiResult.NoServerConfigured -> _syncState.value = SyncState.Failed("还没设置电脑地址。")
            }
        }
        syncJob = job
        job.join()
    }

    // -----------------------------------------------------------------------
    // 同伴数据包（2026-11-21）：把快照导成文件 / 从文件导回来
    // -----------------------------------------------------------------------

    /**
     * 导出：把本机的整份快照打成 `PeerPack` 文件，塞进 `cacheDir/peer-packs/`，
     * 再通过 [shareFile] 交给 UI 弹系统分享面板。
     *
     * **先自检再导出**：如果这台手机自己就是几天前同步的，导出去只会把一份旧数据
     * 伪装成「我刚同步过」。所以自己都没同步过就直接拒绝；时间够旧就提示一句。
     */
    fun exportPack() {
        if (_peerBusy.value) return
        viewModelScope.launch {
            _peerBusy.value = true
            try {
                val syncedAt = snapshot.syncedAt()
                if (syncedAt <= 0L) {
                    _peerMessage.value = "这台手机还没同步过数据，先在设置里点「立即同步」。"
                    return@launch
                }
                val entries = snapshot.entries()
                if (entries.isEmpty()) {
                    _peerMessage.value = "快照是空的，先在设置里点「立即同步」。"
                    return@launch
                }
                val pack = PeerPack(
                    appVersion = BuildConfig.VERSION_NAME,
                    shopName = _shopName.value,
                    baseUrl = _baseUrl.value,
                    syncedAt = syncedAt,
                    syncMonths = serverConfig.syncMonths(),
                    syncFrom = serverConfig.syncFrom(),
                    syncTo = serverConfig.syncTo(),
                    backupKey = serverConfig.backupKey(),
                    entries = entries,
                )
                val file = withContext(Dispatchers.IO) {
                    val dir = File(getApplication<Application>().cacheDir, "peer-packs").apply { mkdirs() }
                    // 先清旧包：目录里堆着几个几天前的包，分享面板里会挑错。
                    dir.listFiles()?.forEach { it.delete() }
                    File(dir, PeerPack.suggestFileName(syncedAt)).apply {
                        writeText(PeerPack.encode(pack), Charsets.UTF_8)
                    }
                }
                _shareFile.value = file
            } catch (e: Exception) {
                _peerMessage.value = "导出失败：${e.message ?: e.javaClass.simpleName}"
            } finally {
                _peerBusy.value = false
            }
        }
    }

    /**
     * 导入：读 [uri] 指向的数据包，校验后**整份替换**本机快照。
     *
     * 顺序里有三件事不能省：
     * 1. **先清 `ResponseCache`**。精确键的优先级高于快照（`AppViewModel` 的 `load`），
     *    不清的话 B 打开过的页面仍会先亮自己的旧版本，出现「有的页新、有的页旧」——
     *    而这正是 B 最需要看数据的离线时刻。
     * 2. **同步区间跟着覆盖**。只换快照不改 `syncMonths/syncFrom/syncTo/backupKey`，
     *    断网本地筛选会按旧区间过滤，导入的条目反而筛不出来。
     * 3. **不自动 syncNow**。B 通常连不上电脑，自动同步只会立刻闪一条红字，
     *    把「导入成功」这件事盖掉。
     */
    fun importPack(uri: Uri) {
        if (_peerBusy.value) return
        viewModelScope.launch {
            _peerBusy.value = true
            try {
                val text = withContext(Dispatchers.IO) {
                    val resolver = getApplication<Application>().contentResolver
                    resolver.openInputStream(uri)?.use { it.readBytes().toString(Charsets.UTF_8) }
                        ?: throw IllegalArgumentException("打不开这个文件。")
                }
                val pack = PeerPack.decode(text).getOrElse { error ->
                    _peerMessage.value = error.message ?: "这个文件读不出来。"
                    return@launch
                }

                // 先记下本机原来的时间：导的是旧包时要提醒。
                // 不弹二次确认（用户在微信里挑的文件，他自己知道挑的是哪份），
                // 但「这份比你原来的旧」必须说出来 —— 否则他会以为数据被更新了。
                val previousSyncedAt = snapshot.syncedAt()

                // 1) 快照整份替换 + 精确键清空
                snapshot.save(pack.syncedAt, pack.entries)
                cache.clear()

                // 2) 同步区间与备份键：它们和 entries 是一套的，必须一起换。
                //    **先清空再写**（A12）：`saveSyncSpan` 对空值是跳过不写，包里的区间字段
                //    为空时（旧版导出端、或同伴没同步过完整区间），三个键会原样留着
                //    **上一家店/上一次同步**的值 —— 断网本地筛选按旧区间过滤，
                //    刚导入的单据反而被筛掉，页面显示「这个区间没有单据」。
                serverConfig.clearSyncSpan()
                serverConfig.saveSyncMonths(pack.syncMonths)
                serverConfig.saveSyncSpan(pack.syncFrom, pack.syncTo, pack.backupKey)
                serverConfig.saveLastSyncAt(pack.syncedAt)

                // 3) 地址兜底：只在从没配置过时才用包里的。
                //    覆盖已有的地址等于把 B 指向一台它可能连不上的电脑，不能做。
                if (_baseUrl.value.isEmpty() && pack.baseUrl.isNotEmpty()) {
                    serverConfig.saveBaseUrl(pack.baseUrl)
                    _baseUrl.value = serverConfig.baseUrl()
                    api.updateBaseUrl(_baseUrl.value)
                }
                // 有数据可看 = 连不上时该进主界面，而不是停在「连不上电脑」页。
                serverConfig.savePairedConfirmed(true)

                // 4) 来源标记：界面要能说出「这是同伴的、截至什么时候」
                val importedAt = System.currentTimeMillis()
                serverConfig.savePeerImported(importedAt, pack.shopName)
                _peerImportedAt.value = importedAt
                _peerSourceShop.value = pack.shopName
                if (pack.shopName.isNotBlank()) {
                    _shopName.value = pack.shopName
                    serverConfig.saveShopName(pack.shopName)
                }

                // 5) 界面状态与刷新
                _cacheNewest.value = maxOf(cache.newestTimestamp() ?: 0L, pack.syncedAt)
                _lastSyncAt.value = pack.syncedAt
                _syncMonths.value = pack.syncMonths
                _syncState.value = SyncState.Idle
                _bootstrapped.value = true
                _connectionLost.value = false
                _needsPairing.value = false
                // 导入成功本身就是「数据在手机上」，所以从这一刻起 401 按离线处理（A3）——
                // 否则紧接着的那几个请求一拿到 401，又把刚导入的东西顶去配对页。
                _needsSetup.value = _baseUrl.value.isEmpty()
                refreshOpenedPages()

                val whenText = formatCacheTime(pack.syncedAt)
                val olderNote = if (previousSyncedAt > pack.syncedAt) {
                    "（注意：这份比这台手机原来的旧，原来截至 ${formatCacheTime(previousSyncedAt)}）"
                } else {
                    ""
                }
                _peerMessage.value = if (_needsSetup.value) {
                    "已导入 ${pack.entryCount} 项（数据截至 $whenText）$olderNote" +
                        "，但数据包里没有电脑地址，请先填地址才能进主界面。"
                } else {
                    "已导入 ${pack.entryCount} 项，数据截至 $whenText。$olderNote"
                }
            } catch (e: Exception) {
                _peerMessage.value = "导入失败：${e.message ?: e.javaClass.simpleName}"
            } finally {
                _peerBusy.value = false
            }
        }
    }

    /** 设置页「重新检测」：重跑一次启动流程（地址/配对/元信息）。 */
    fun recheck() {
        // 「重新检测」是用户明确表示「我要重新连」：撤销「先看离线缓存」这个临时状态（A2），
        // 否则连不上的时候会一直按离线放行，用户会以为检测按钮没反应。
        offlineBrowse = false
        viewModelScope.launch {
            _bootstrapped.value = false
            bootstrap()
        }
    }

    /**
     * 提交电脑地址（首次配置与「改地址」共用同一条路径）。
     *
     * 两条硬规则，都是 2026-11 审计挖出来的（A1 / A4）：
     *
     * 1. **只有真的换了地址才清东西**。同一条地址原样提交必须什么都不动 ——
     *    原来「改地址」入口直接 `saveBaseUrl("")`，等于每次点一下就清空 cookie
     *    和「确认过配对」，紧接着 bootstrap 又因为「地址空 + 有快照」把 `needsSetup`
     *    按回 false，界面既没有输入框也不是主界面，各页永远转圈（死状态）。
     * 2. **真的换店时清缓存**。缓存键不含 host（`ResponseCache` / `SnapshotStore`），
     *    旧店的客户欠款和商品价留着，断网时会当成新店的数据显示出来 —— 报价报错价
     *    是钱的问题。两个键清完，界面上的时间标签也必须归零，不能残留「截至某时」。
     */
    fun saveBaseUrl(raw: String, onDone: (String) -> Unit = {}) {
        viewModelScope.launch {
            // 旧地址要在写盘之前取：DataStore 是权威来源，内存值只兜「刚写入还没落盘」。
            val previous = serverConfig.baseUrl().ifEmpty { _baseUrl.value }
            serverConfig.saveBaseUrl(raw)
            val normalized = serverConfig.baseUrl()
            _baseUrl.value = normalized
            api.updateBaseUrl(normalized)
            val switchedComputer = normalized.isNotEmpty() && previous.isNotEmpty() && normalized != previous
            if (switchedComputer) switchToNewComputer(previous)
            // 「改地址」提交成功 = 用户要重新连，撤销「先看离线缓存」的临时状态（A2）。
            offlineBrowse = false
            _connectionLost.value = false
            _needsSetup.value = normalized.isEmpty()
            _bootstrapped.value = false
            _meta.value = null
            bootstrap()
            onDone(normalized)
        }
    }

    /**
     * 换到**另一台电脑**（另一家店）时要丢掉的本机数据（A4）。
     *
     * 与 [disconnect] 的区别：断开连接**不清缓存**（明确需求 —— 重新连回来不该再等
     * 一次全量加载）；而换店时旧数据是别家的，留着会误导报价，必须清干净。
     */
    private suspend fun switchToNewComputer(previousUrl: String) {
        // 旧 cookie 只属于旧主机：按主机清（A6），别的电脑的配对态不该受影响。
        // 必须用 `api` 内部那个实例，否则清的不是真正发请求时用的那份。
        // 地址解析不出来（脏数据）时退回全清 —— 这时说不清旧主机是谁，
        // 而「把别家的凭据留在手机上」比「多配对一次」更糟。
        // 放到 IO 线程：`clear()` 内部是同步落盘（`commit()`，见 ApiClient 的 A8 说明），
        // 而这个方法是从 `viewModelScope`（主线程）进来的，SharedPreferences 的
        // 同步写盘不该出现在主线程上。
        withContext(Dispatchers.IO) {
            val previousHost = CookiePersistence.hostKeyOf(previousUrl)
            if (previousHost != null) api.cookieStore.clear(previousHost) else api.cookieStore.clear()
        }

        // 「确认过配对」作废：新地址上这台手机还没被允许过。不清的话连不上时
        // 会被当成「配过对」直接放行进主界面，看到的却是上一家店的缓存。
        serverConfig.savePairedConfirmed(false)

        // 换店 = 旧数据是别家的。清缓存 + 快照 + 同步区间（区间指向旧快照的布局）。
        cache.clear()
        snapshot.clear()
        serverConfig.clearSyncSpan()
        // 同伴来源也跟着作废：那份数据属于旧店，留着界面会写「数据来自同伴（旧店）」。
        serverConfig.clearPeerImported()

        // 内存态归零：不清的话设置页会继续显示「断网时能看截至 X 的数据」。
        _cacheNewest.value = 0L
        _lastSyncAt.value = 0L
        _peerImportedAt.value = 0L
        _peerSourceShop.value = ""
        _syncState.value = SyncState.Idle
        // 换电脑就等于换店：旧店名不能留在标题上。
        _shopName.value = ""
        // 页面上已经渲染的数据一律退回 Loading：否则从主界面改地址的那一瞬间，
        // 屏幕上仍是旧店的客户欠款，看上去像「新电脑立刻有数据」。
        _today.value = UiState.Loading
        _customers.value = UiState.Loading
        _orders.value = UiState.Loading
        _products.value = UiState.Loading
        _customerDetail.value = UiState.Loading
        _documentDetail.value = UiState.Loading
        _productDetail.value = UiState.Loading
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
            // 全清（不传 hostKey）：「断开连接」的语义是「这台手机不再连这台电脑」，
            // 粒度上就是清掉所有 cookie（A6 保留了这个默认行为）。
            api.cookieStore.clear()
            serverConfig.clear()
            _baseUrl.value = ""
            api.updateBaseUrl("")
            // 断开后回到配置页，「先看离线缓存」这个临时状态也该失效（A2）。
            offlineBrowse = false
            // 缓存**不清**：断开是「不再连这台电脑」，不是「忘掉看过的数据」。
            // 清了的话用户重新连回来要重新等一遍全量加载，没有道理。
            _meta.value = null
            // 公司名要清：断开的语义就是「不再连这台电脑」，留着会让下一次
            // 连到别的店时标题先闪一下旧店名（`ServerConfig.clear()` 已一并清盘）。
            _shopName.value = ""
            _needsPairing.value = false
            _connectionLost.value = false
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
                // 2026-11-21 真机验证抓到的 bug：这里原来只取 `ResponseCache` 的时间戳，
                // 而导入同伴数据包时 `ResponseCache` 是**被清空的**（不清会新旧混显），
                // 于是设置页明明有 255 项缓存，却显示「还没有缓存」。
                // 快照时间必须一起算进来。
                _cacheNewest.value = maxOf(cache.newestTimestamp() ?: 0L, snapshot.syncedAt())
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

    /**
     * 只刷新**用户已经点开过的**页面。
     *
     * 为什么导入同伴数据包后用这个而不是 [refreshAll]（A3）：B 通常连不上电脑，
     * `refreshAll()` 就是四个请求同时撞 401/超时。而且它会把四个页面从
     * `Loading` 拽进一次网络等待 —— 刚导入完的一瞬间，屏幕上本该立刻是导入的数据。
     *
     * 只刷「已经 Ready 的页」还有一个好处：这些页此刻显示的是**导入前**的数据
     * （B 自己看过的旧版本），不刷就会新旧混显；而没点开过的页保持 Loading，
     * 用户点进去时 `load()` 直接从快照出数（`cache.clear()` 之后快照是唯一来源，
     * 正好是刚导入的那份）。
     */
    private fun refreshOpenedPages() {
        if (_today.value is UiState.Ready) refreshToday()
        if (_customers.value is UiState.Ready) refreshCustomers()
        if (_orders.value is UiState.Ready) refreshOrders()
        if (_products.value is UiState.Ready) refreshProducts()
    }

    // -----------------------------------------------------------------------
    // 配对
    // -----------------------------------------------------------------------

    /** 配对提交进行中：配对页的按钮据此禁用（A11，防连点）。 */
    private val _pairBusy = MutableStateFlow(false)
    val pairBusy: StateFlow<Boolean> = _pairBusy.asStateFlow()

    fun submitPairCode(code: String, onResult: (String?) -> Unit) {
        // 防连点（A11）。6 位码一输满按钮就亮，但请求要 200ms~1.5s 才回来；
        // 这期间再点一次会发出**第二个** `/m/api/pair`：
        // - 服务端那 3 次/分钟的限流被自己吃掉；
        // - 两次响应可能一个成功、一个「配对码已使用」，界面上谁后到算谁的 ——
        //   真机上表现为「明明配对成功了却又弹出一句错误」。
        // 复用 `peerBusy` 那套「忙就禁用」的模式，UI 只看状态，不在 Composable 里做判断。
        if (_pairBusy.value) return
        _pairBusy.value = true
        viewModelScope.launch {
            try {
                // platform=app：让电脑端设备列表能区分「手机 App」和「浏览器」。
                // 不带这个字段的话，一个 App 和一个浏览器在设置页会显示成两条
                // 一模一样的「Android 手机」，撤销设备时没法判断该撤哪条。
                //
                // 2026-10-24 两处改进：
                // ① `label` 用手机型号（Build.MODEL，如 `2201123C`）而不是「手机 App」——
                //    两条记录连名字都一样时，人眼根本分不出来；
                // ② `device_uid` 是这台手机的**稳定身份**，电脑端据此把同一台设备的重复
                //    配对合并成一条（用户实测：改了地址再配一次就多出一条）。
                val uid = serverConfig.deviceUid()
                val label = pairLabel()
                val payload = buildString {
                    append("{")
                    append("\"code\":\"").append(code.jsonEscape()).append("\",")
                    append("\"label\":\"").append(label.jsonEscape()).append("\",")
                    append("\"platform\":\"app\",")
                    append("\"device_uid\":\"").append(uid.jsonEscape()).append("\"")
                    append("}")
                }
                when (val result = api.postJson("/m/api/pair", payload)) {
                    is ApiResult.Success -> {
                        _needsPairing.value = false
                        // 配对成功后「先看离线缓存」这个临时状态就没意义了：之后是正常在线。
                        offlineBrowse = false
                        // 先落「确认过配对」再 bootstrap：配对刚成功、网络随即抖一下时，
                        // 也不会被当成「从没连上过」而退回连不上页。
                        serverConfig.savePairedConfirmed(true)
                        bootstrap()
                        // 配对成功立刻同步一次：这样店主刚配好、还没点任何页面时，
                        // 关电脑前的「最后同步」就已经有值了，不是「从未同步」。
                        syncNow()
                        onResult(null)
                    }
                    is ApiResult.Failure -> onResult(result.message)
                    is ApiResult.Unreachable -> onResult("连不上电脑，请确认手机和电脑在同一个 WiFi。")
                    ApiResult.Unauthorized -> onResult("配对被拒绝，请在电脑上重新生成配对码。")
                    ApiResult.NoServerConfigured -> onResult("还没设置电脑地址。")
                }
            } finally {
                _pairBusy.value = false
            }
        }
    }

    /** 配对时上报的设备名：优先手机型号（`Xiaomi 2201123C` → `2201123C`）。 */
    private fun pairLabel(): String {
        val model = runCatching { android.os.Build.MODEL }.getOrNull().orEmpty().trim()
        return if (model.isEmpty()) "手机 App" else model.take(40)
    }

    private fun String.jsonEscape(): String = replace("\\", "\\\\").replace("\"", "\\\"")

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
        val state = load(
            publish = { _orders.value = it },
            cacheKey = "orders:${query.toSortedMap()}",
            path = "/m/api/orders",
            query = query,
            serializer = { text -> api.decode(text, OrdersResponse.serializer()) },
        )
        // 断网 + 精确缓存没命中（自定义区间没有一一对应的快照键）→ 用备份列表本地筛选。
        // 只在「连不上」时兜底；服务端明确报错仍原样显示，不能拿旧数据盖住真实错误。
        _orders.value = if (state is UiState.Failed && state.message.contains("连不上电脑")) {
            fallbackOrdersFromBackup(range, _orderType.value, _orderCustomerId.value) ?: state
        } else {
            state
        }
    }

    /**
     * 断网且当前时间区间没有精确缓存时，用「离线备份列表」在本地按区间 + 类型 + 客户筛一次。
     *
     * 为什么需要：快照只给「今天/本周/本月/本年/全部」这些**快捷档**各存一份，用户按
     * 「10 月」「10/31 单日」「8–10 月」这种自定义区间查时，缓存里没有对应键 —— 过去就
     * 直接报「连不上电脑」（2026-11-01 用户报的「11 月 1 号一过就查不到 10 月」）。备份列表
     * 覆盖整段同步区间（默认本月 + 上月），本地筛一下就能翻到。
     *
     * 只处理**显式起止区间**：周/月/年这些「具体是哪几天由服务端算」的档不在这里算，
     * 避免把日期换算规则复制到客户端、两端口径分叉（`resolve_mobile_range` 只有一份）。
     */
    private suspend fun fallbackOrdersFromBackup(
        range: DateRange,
        orderType: String,
        customerId: Int,
    ): UiState<OrdersResponse>? {
        val start = range.startDate
        val end = range.endDate
        if (start.isEmpty() || end.isEmpty()) return null
        val backupKey = serverConfig.backupKey()
        if (backupKey.isEmpty()) return null
        val backup = snapshot.get(backupKey) ?: return null
        val decoded = api.decode(backup.body, OrdersResponse.serializer()) ?: return null
        val syncFrom = serverConfig.syncFrom()
        val documents = OfflineOrderFilter.filter(decoded.documents, start, end, orderType, customerId)
        // 请求区间早于「已同步起点」→ 更早的单根本没缓存，标记不完整，页面提示需联网。
        val truncated = decoded.truncated || (syncFrom.isNotEmpty() && start < syncFrom)
        _cacheNewest.value = maxOf(_cacheNewest.value, backup.fetchedAt)
        return UiState.Ready(
            OrdersResponse(
                schema = decoded.schema,
                ok = true,
                documents = documents,
                truncated = truncated,
                orderType = orderType,
                customerId = customerId,
                customerName = "",
                dateRange = range,
            ),
            fromCache = true,
            fetchedAt = backup.fetchedAt,
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

    /**
     * 打开商品详情并带上页内筛选（搜索/排序/方向）。
     *
     * 只有一个入口（不再另写一个无参版本）：Kotlin 里 `f(id)` 会同时匹配
     * `f(id)` 与 `f(id, default)` 两个重载，编译期就报歧义。
     *
     * 换商品时**重置页内筛选**：在商品 A 里搜过「老李」，退回列表点开商品 B
     * 却还带着这个搜索词，用户会以为 B 只有那一个客户。
     *
     * 缓存键必须含这三个条件：同一个商品在「拼音正序」和「价格倒序」下是两份不同的列表，
     * 共用键会让离线时看到另一个排序的结果 —— 看起来像「排序没生效」
     * （2026-10-22 商品列表上踩过同一个坑）。
     */
    fun openProduct(productId: Int, resetScroll: Boolean = false) {
        if (productId <= 0) return
        val loadedId = (_productDetail.value as? UiState.Ready)?.value?.product?.id ?: 0
        if (loadedId != 0 && loadedId != productId) {
            // 换了一个商品：把上一个商品的搜索/排序带回新页面是错的。
            _productDetailQuery.value = ""
            _productDetailSortPinyin.value = false
            _productDetailDescending.value = true
        }
        viewModelScope.launch {
            val q = _productDetailQuery.value.trim()
            val sort = if (_productDetailSortPinyin.value) "pinyin" else "price"
            val order = if (_productDetailDescending.value) "desc" else "asc"
            val cacheKey = "product:$productId:$sort:$order:$q"
            // 只在「手上还没有数据」时才转圈：换排序时保持旧列表在屏幕上，
            // 否则每点一次箭头都白屏闪一下。
            if (_productDetail.value !is UiState.Ready) _productDetail.value = UiState.Loading
            val params = buildMap {
                if (q.isNotEmpty()) put("q", q)
                put("sort", sort)
                put("order", order)
            }
            _productDetail.value = load(
                cacheKey = cacheKey,
                path = "/m/api/products/$productId",
                query = params,
                serializer = { text -> api.decode(text, ProductDetailResponse.serializer()) },
            )
            // 回顶信号必须在新数据发布之后置位（见 customersScrollReset 的注释）。
            if (resetScroll) _productDetailScrollReset.value = true
        }
    }

    /** 详情页搜索：防抖后重新请求（与列表页同一套 220ms 节奏）。 */
    fun onProductDetailQueryChange(value: String) {
        _productDetailQuery.value = value
        searchJob?.cancel()
        searchJob = viewModelScope.launch {
            delay(220)
            // 换搜索词等于换一批数据：同样要回顶，否则会停在旧结果的行号上。
            openProduct(currentProductId(), resetScroll = true)
        }
    }

    /** 换排序依据：价格 ↔ 客户拼音。换档时重置为该档的默认方向。 */
    fun toggleProductDetailSort() {
        _productDetailSortPinyin.value = !_productDetailSortPinyin.value
        // 价格默认「贵的在前」（desc），拼音默认 A→Z（asc）。
        // 不重置的话，用户从「价格正序」切到拼音会看到 Z→A，很怪。
        _productDetailDescending.value = !_productDetailSortPinyin.value
        openProduct(currentProductId(), resetScroll = true)
    }

    /** 正倒序切换：只反转方向，不换排序依据。 */
    fun toggleProductDetailOrder() {
        _productDetailDescending.value = !_productDetailDescending.value
        openProduct(currentProductId(), resetScroll = true)
    }

    /** 当前正在看的商品 id（从已加载的详情里取；还没加载时返回 0，调用方会忽略）。 */
    private fun currentProductId(): Int =
        (_productDetail.value as? UiState.Ready)?.value?.product?.id ?: 0

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
        //
        //    2026-10-24：精确键没命中时**再查一次全量快照**。这是「二级页面断网
        //    刷不出来」的修法 —— 详情页只在点开那一刻才落盘，从没点开过的单据
        //    断网必然空白；快照里已经带着它的详情。
        // 先取缓存。**不管有没有 publish 都要取**：下面「没有电脑地址」的分支
        // 需要用缓存当最终结果，而不是把一个转圈返回给页面。
        //
        // 解不开的缓存**就当没有**（A12）：精确键读出来但 decode 失败时继续去查快照，
        // 而不是直接放弃这条回落链 —— 一份坏数据（schema 变了、上次写了一半）
        // 不该把快照里那份能看的旧数据挡掉。
        val exact = cache.get(cacheKey)
        val exactValue: T? = exact?.let { serializer(it.body) }
        val snap = if (exactValue == null) snapshot.get(SnapshotStore.canonicalKey(path, query)) else null
        val cachedValue: T? = exactValue ?: snap?.let { serializer(it.body) }
        val cached = if (exactValue != null) exact else snap
        var cachedState: UiState.Ready<T>? = null
        if (cached != null && cachedValue != null) {
            _cacheNewest.value = maxOf(_cacheNewest.value, cached.fetchedAt)
            // 显式收窄到 T：`cachedValue` 的静态类型是 T?，直接塞进 Ready<T> 会被推成 Ready<T?>。
            val value: T = cachedValue
            val state = UiState.Ready(value, fromCache = true, fetchedAt = cached.fetchedAt)
            cachedState = state
            publish?.invoke(state)
            // 2026-11-21：没有电脑地址（同伴导入后地址还没填、或刚被清掉）时，
            // 请求必然发不出去。与其返回 NeedsSetup 让页面转圈，不如就把缓存当结果 ——
            // 人打开 App 本来就是为了看这份数据。
            if (_baseUrl.value.isEmpty()) return state
        }
        // 显式写 <T>：`serializer` 的类型是 (String) -> T?，不指定的话编译器会把
        // loadWithCache 的 T 推成 T?，得到 Loaded<T?> 而无法赋给 UiState<T>。
        return when (
            val outcome = api.loadWithCache<T>(
                cache = cache,
                cacheKey = cacheKey,
                path = path,
                query = query,
                decode = serializer,
                snapshot = snapshot,
                // 401 按离线处理的条件（同伴导入的数据 / 用户主动选了看缓存），
                // 纯逻辑见 StartupRouting.requiresPairingOnUnauthorized（A3/A2）。
                offlineAllowed = !requiresPairingOnUnauthorized(),
            )
        ) {
            is LoadOutcome.Ok -> {
                val data: Loaded<T> = outcome.data
                if (data.fromCache) {
                    _cacheNewest.value = maxOf(_cacheNewest.value, data.fetchedAt)
                }
                UiState.Ready(data.value, data.fromCache, data.fetchedAt)
            }
            is LoadOutcome.Error -> {
                // 服务端回了内容但解不开时，**不要**用一个「数据格式不对」把屏幕上
                // 本来能看的旧数据盖掉（A12）：能读到缓存的场景下，旧数据是用户唯一
                // 能用的东西，把它换成一句错误提示纯属倒退。
                // 服务端明确报错（4xx/5xx）仍然照原样显示，不许拿旧数据糊过去。
                if (outcome.kind == LoadOutcome.Error.Kind.BAD_PAYLOAD && cachedState != null) {
                    cachedState
                } else {
                    UiState.Failed(outcome.message)
                }
            }
            LoadOutcome.NeedsPairing -> {
                _needsPairing.value = true
                UiState.NeedsPairing
            }
            LoadOutcome.NeedsSetup -> {
                // 2026-11-21：本地有数据时不把人弹回配置页 —— 数据就在手机上，
                // 弹走等于把刚看到的东西抢走。只有真的什么都没有时才去填地址。
                if (_cacheNewest.value <= 0L) _needsSetup.value = true
                UiState.NeedsSetup
            }
        }
    }
}
