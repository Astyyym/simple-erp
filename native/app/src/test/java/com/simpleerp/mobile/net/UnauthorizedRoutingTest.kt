package com.simpleerp.mobile.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 401 处置的契约测试（2026-11 审计 A3 / A2）。
 *
 * 事故形状：B 的手机导入了 A 导出的同伴数据包（1.4.13 的功能），导入后
 * `refreshAll()` + `maybeAutoSync()` 立刻发请求，而 B 从没和这台电脑配对过 ——
 * 四个请求全 401 → `_needsPairing = true` → 全屏配对页，刚导入的数据一眼都看不到。
 *
 * 所以这里的核心断言是：**处在这两种「本地本来就有数据」状态下，401 不许把人顶走**。
 */
class UnauthorizedRoutingTest {

    /** 正常情况（没有同伴数据、也没选看缓存）：401 就是该去输码。 */
    @Test
    fun testPlainUnauthorizedRequiresPairing() {
        assertTrue(StartupRouting.requiresPairingOnUnauthorized(peerImportedAt = 0L))
    }

    /** 同伴导入状态：数据在手机上，401 是常态，按离线处理。 */
    @Test
    fun testPeerImportedDataDoesNotRequirePairing() {
        assertFalse(StartupRouting.requiresPairingOnUnauthorized(peerImportedAt = 1_700_000_000_000L))
        // 只要有标记（非 0）就算，不依赖它是多久以前。
        assertFalse(StartupRouting.requiresPairingOnUnauthorized(peerImportedAt = 1L))
    }

    /** 用户在配对页按了「先看离线缓存」：他自己选择了离线，不许再把他弹回去。 */
    @Test
    fun testUserChoseOfflineCacheDoesNotRequirePairing() {
        assertFalse(
            StartupRouting.requiresPairingOnUnauthorized(
                peerImportedAt = 0L,
                userChoseOfflineCache = true,
            ),
        )
    }

    /** 启动路由：同样两个状态不许停在配对页（否则 `bootstrap` 一遍又把数据锁回去）。 */
    @Test
    fun testStartupRouteRespectsOfflineAllowed() {
        assertEquals(
            StartupRoute.PAIR,
            StartupRouting.route(MetaProbe.NOT_PAIRED, pairedConfirmed = false),
        )
        assertEquals(
            StartupRoute.MAIN,
            StartupRouting.route(
                MetaProbe.NOT_PAIRED,
                pairedConfirmed = false,
                offlineAllowed = true,
            ),
        )
    }

    /** 「按离线放行」只影响 401 那一格，不能把「连不上 + 从没确认配对」也放进去。 */
    @Test
    fun testOfflineAllowedDoesNotOpenConnectionLostGate() {
        assertEquals(
            StartupRoute.CONNECTION_LOST,
            StartupRouting.route(
                MetaProbe.UNREACHABLE,
                pairedConfirmed = false,
                offlineAllowed = true,
            ),
        )
    }
}
