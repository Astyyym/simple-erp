package com.simpleerp.mobile.net

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 启动路由的契约测试（2026-10-31）。
 *
 * 这些用例钉的是一次**真实事故**：门店电脑的防火墙被点了「取消」→ Block 规则 →
 * 手机发出的包全被丢弃 → `meta` 永远超时 → 当时的代码把「超时」当成「不用配对」，
 * 于是 App 静默进主界面显示旧缓存，看起来像「已经配好了」。
 *
 * 所以这里的核心断言只有一句：**没确认过配对 + 连不上 = 不许进主界面**。
 * 同时必须保住另一条既有能力：**确认过配对 + 连不上 = 照旧进主界面看缓存**
 * （关电脑前同步一次、之后断网也能看，是明确需求）。
 */
class StartupRoutingTest {

    @Test
    fun testPairedGoesToMainEvenWhenNeverConfirmed() {
        // 服务端说「配对了」就是权威结论，本地标志缺失（清过数据）也不该拦。
        assertEquals(StartupRoute.MAIN, StartupRouting.route(MetaProbe.PAIRED, false))
        assertEquals(StartupRoute.MAIN, StartupRouting.route(MetaProbe.PAIRED, true))
    }

    @Test
    fun testNotPairedGoesToPairScreenRegardlessOfLocalFlag() {
        // 服务端明确说没配对（或 401）：本地那个 true 已经过期了，必须去输码。
        assertEquals(StartupRoute.PAIR, StartupRouting.route(MetaProbe.NOT_PAIRED, true))
        assertEquals(StartupRoute.PAIR, StartupRouting.route(MetaProbe.NOT_PAIRED, false))
    }

    @Test
    fun testUnreachableWithoutConfirmationShowsConnectionLost() {
        // 事故那条路径：从没确认过配对、又连不上 → 不许静默进主界面。
        assertEquals(
            StartupRoute.CONNECTION_LOST,
            StartupRouting.route(MetaProbe.UNREACHABLE, false),
        )
    }

    @Test
    fun testUnreachableWithConfirmationStillAllowsOfflineCache() {
        // 既有能力：配过对、电脑只是关机 → 仍进主界面看缓存（别被这次修复弄坏）。
        assertEquals(StartupRoute.MAIN, StartupRouting.route(MetaProbe.UNREACHABLE, true))
    }

    @Test
    fun testServerErrorWithoutConfirmationShowsConnectionLost() {
        // 地址被别的设备拿走了：HTTP 通了但不是简单ERP，同样算「没连上」。
        assertEquals(
            StartupRoute.CONNECTION_LOST,
            StartupRouting.route(MetaProbe.SERVER_ERROR, false),
        )
    }

    @Test
    fun testServerErrorWithConfirmationDoesNotBlockTheUser() {
        // 网络抖动/服务端 500 不该把人挡在主界面外：配过对就让他看缓存。
        assertEquals(StartupRoute.MAIN, StartupRouting.route(MetaProbe.SERVER_ERROR, true))
    }
}
