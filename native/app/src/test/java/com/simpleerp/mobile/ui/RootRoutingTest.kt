package com.simpleerp.mobile.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 顶层页面判定的回归测试（2026-11 审计 A1）。
 *
 * 事故形状：三个「改地址」入口都调 `saveBaseUrl("")` —— 地址、cookie、
 * 「确认过配对」一起清掉；随后 `bootstrap()` 在「地址空 + 本地有快照」分支
 * 把 `needsSetup` 按回 false，而 `setupForChange` 那个标志虽然被置了 true、
 * **从来没有任何分支读它**。结果：既不是配置页（`needsSetup` 为假，没有输入框），
 * 也不是主界面（各页 `NeedsSetup` 渲染成转圈），用户只能杀进程。
 *
 * 所以这里钉的第一条就是：`setupForChange` 为真时必须进 SetupScreen。
 */
class RootRoutingTest {

    private fun screen(
        bootstrapped: Boolean = true,
        needsSetup: Boolean = false,
        setupForChange: Boolean = false,
        connectionLost: Boolean = false,
        needsPairing: Boolean = false,
    ) = RootRouting.screen(bootstrapped, needsSetup, setupForChange, connectionLost, needsPairing)

    /** 冷启动探测没结束前不许渲染任何实页：否则会先闪一下配对页再跳主界面。 */
    @Test
    fun testNotBootstrappedAlwaysLoading() {
        assertEquals(RootScreen.LOADING, screen(bootstrapped = false))
        assertEquals(
            RootScreen.LOADING,
            screen(bootstrapped = false, needsSetup = true, needsPairing = true),
        )
    }

    /** 首次配置：没有地址 → 配置页。 */
    @Test
    fun testNeedsSetupShowsSetup() {
        assertEquals(RootScreen.SETUP, screen(needsSetup = true))
    }

    /**
     * **本次修复的核心**：地址还在（`needsSetup` 为假）、用户主动点了「改地址」，
     * 也必须进配置页，否则点了等于没点。
     */
    @Test
    fun testSetupForChangeWinsEvenWhenAddressStillUsable() {
        assertEquals(RootScreen.SETUP, screen(setupForChange = true))
        // 与其它标志同时为真时也不能被吞掉（历史 bug 就是这个组合没被处理）。
        assertEquals(
            RootScreen.SETUP,
            screen(setupForChange = true, connectionLost = true),
        )
        assertEquals(
            RootScreen.SETUP,
            screen(setupForChange = true, needsPairing = true),
        )
    }

    @Test
    fun testConnectionLostAndPairingStillRoute() {
        assertEquals(RootScreen.CONNECTION_LOST, screen(connectionLost = true))
        assertEquals(RootScreen.PAIR, screen(needsPairing = true))
        // 两个都为真时先查网络：让人去输一个必然失败的配对码没意义。
        assertEquals(
            RootScreen.CONNECTION_LOST,
            screen(connectionLost = true, needsPairing = true),
        )
    }

    @Test
    fun testNothingWrongGoesToMain() {
        assertEquals(RootScreen.MAIN, screen())
    }

    /** 取消出口只在「主动改地址 + 手上还有地址」时给（A1 的第 3 条要求）。 */
    @Test
    fun testCancelOnlyWhenChangingAnExistingAddress() {
        assertTrue(RootRouting.canCancelSetup(setupForChange = true, baseUrl = "http://192.168.10.5:5001"))
        // 首次配置：地址为空，取消了就是一块空白，不能给。
        assertFalse(RootRouting.canCancelSetup(setupForChange = true, baseUrl = ""))
        assertFalse(RootRouting.canCancelSetup(setupForChange = true, baseUrl = "   "))
        // 不是「改地址」进来的（例如断开连接后），也没有可返回的主界面。
        assertFalse(RootRouting.canCancelSetup(setupForChange = false, baseUrl = "http://192.168.10.5:5001"))
    }
}
