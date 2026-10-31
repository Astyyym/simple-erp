package com.simpleerp.mobile.net

/**
 * 启动时对 `/m/api/meta` 的一次探测结果（只保留「决定去哪个页面」需要的信息）。
 *
 * 为什么要把它从 [ApiResult] 里拆出来：这个判定必须能在 **JVM 单测**里覆盖，
 * 而 `ApiResult` 与 Android 网络栈同处一个文件（`ApiClient.kt` 顶部 import 了
 * `android.webkit.CookieManager`）。用枚举表达「探测结论」后，
 * 路由函数只依赖纯 Kotlin，单测不必碰 Android 类加载。
 */
enum class MetaProbe {
    /** 200 且 `paired=true`：服务端认得这台手机。 */
    PAIRED,

    /** 200 且 `paired=false`，或 401：地址通了，但这台手机还没配对 / 已被撤销。 */
    NOT_PAIRED,

    /** 连不上：超时、拒绝连接、DNS 失败。**最常见的其实是电脑没开、不同 WiFi、防火墙拦了。** */
    UNREACHABLE,

    /** 连上了但对方不像简单ERP（解析失败），或回了 4xx/5xx。 */
    SERVER_ERROR,
}

/** 启动后该停在哪个页面。 */
enum class StartupRoute {
    MAIN,
    PAIR,
    CONNECTION_LOST,
}

/**
 * 「连不上电脑」到底该不该放行到主界面 —— 这个判断在 2026-10-31 出过一次真实事故。
 *
 * **事故经过**：门店那台电脑的防火墙被点了「取消」，生成一条 Block 规则（以后不再询问）。
 * 手机发出的包全部被丢弃，`/m/api/meta` 永远超时。而当时的代码把「超时」直接当成
 * 「不需要配对」（`needsPairing = false`），于是 App 静默进了主界面、显示 10 月 24 日的旧缓存。
 * 用户看到屏幕上有数据，以为是「配上了」，实际一个包都没到电脑 ——
 * 电脑端「已配对设备 0 台」「最后一次手机成功请求 —」两个人都看不出问题在哪。
 *
 * **修法不是**「连不上就一律弹配对页」：那会把「配过对、只是电脑关机了」这个
 * 明确需求（关电脑前同步一次，之后断网也能看）也一起打断。所以必须区分两种「连不上」：
 *
 * | 探测结果 | 本地确认过配对 | 去哪里 |
 * |---|---|---|
 * | 已配对 | 任意 | 主界面 |
 * | 未配对 / 401 | 任意 | 配对页（服务端明确说了没配对） |
 * | 连不上 | **是** | 主界面 + 离线横幅（既有能力，必须保住） |
 * | 连不上 | **否** | 「连不上电脑」页（本次新增） |
 * | 连上了但不对 | 是 | 主界面（网络抖动不该挡人） |
 * | 连上了但不对 | 否 | 「连不上电脑」页 |
 *
 * 「本地确认过配对」= `ServerConfig.pairedConfirmed`：只有服务端真的回过
 * `paired=true`（或配对提交成功）才会置 true，改地址/断开时清掉。
 */
object StartupRouting {

    fun route(probe: MetaProbe, pairedConfirmed: Boolean): StartupRoute = when (probe) {
        MetaProbe.PAIRED -> StartupRoute.MAIN
        MetaProbe.NOT_PAIRED -> StartupRoute.PAIR
        MetaProbe.UNREACHABLE,
        MetaProbe.SERVER_ERROR,
        -> if (pairedConfirmed) StartupRoute.MAIN else StartupRoute.CONNECTION_LOST
    }
}
