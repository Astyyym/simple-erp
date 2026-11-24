package com.simpleerp.mobile.ui

/**
 * 顶层该显示哪一页。
 *
 * 为什么把这个判定从 `AppRoot` 的 `when` 里抽出来（2026-11 审计 A1）：它是一堆
 * 布尔标志的组合，而「哪个标志说了算」正是那次死状态的根源 ——
 * 「改地址」把 `setupForChange` 置了 true 却没有分支读它，于是界面既不是配置页
 * 也不是主界面，所有页面渲染成转圈。抽成纯函数之后，组合矩阵可以在 JVM 单测里
 * 逐格钉住，不必靠真机点。
 */
enum class RootScreen {
    /** 启动探测还没结束：全屏 Loading。 */
    LOADING,

    /** 填/改电脑地址。 */
    SETUP,

    /** 连不上电脑（地址不通、对方不是简单ERP）。 */
    CONNECTION_LOST,

    /** 电脑在，但这台手机没被允许：输 6 位码。 */
    PAIR,

    /** 主界面（含「按离线用」的情况）。 */
    MAIN,
}

object RootRouting {

    /**
     * 判定顺序**就是优先级**，改动的理由写在这里：
     *
     * 1. `bootstrapped` 最优先 —— 还没探测完就渲染任何页面都会闪一下错的（冷启动时
     *    先闪配对页再跳主界面，用户以为被踢掉了）。
     * 2. `needsSetup || setupForChange` 第二（A1 的修法）：用户在**主动**改地址，
     *    这时哪怕 `needsSetup` 是 false（地址还在、cookie 还在）也必须给输入框，
     *    否则「改地址」点了等于没点。两个条件用 `||`，`setupForChange` 不能被任何
     *    后续分支吞掉。
     * 3. 「连不上」先于「要配对」：两者同时成立时（地址换了 + 从没配对过），
     *    让人先查网络比让他输一个必然失败的码有用。
     */
    fun screen(
        bootstrapped: Boolean,
        needsSetup: Boolean,
        setupForChange: Boolean,
        connectionLost: Boolean,
        needsPairing: Boolean,
    ): RootScreen = when {
        !bootstrapped -> RootScreen.LOADING
        needsSetup || setupForChange -> RootScreen.SETUP
        connectionLost -> RootScreen.CONNECTION_LOST
        needsPairing -> RootScreen.PAIR
        else -> RootScreen.MAIN
    }

    /**
     * 配置页要不要给「取消」出口（A1）。
     *
     * 只有「用户主动改地址」并且**手上还有可用地址**时才给：这时取消可以无害地
     * 回到原页面（地址、cookie、缓存一个都没动过）。首次配置（地址为空）不能给 ——
     * 无处可回，取消了就是一块空白。
     */
    fun canCancelSetup(setupForChange: Boolean, baseUrl: String): Boolean =
        setupForChange && baseUrl.isNotBlank()
}
