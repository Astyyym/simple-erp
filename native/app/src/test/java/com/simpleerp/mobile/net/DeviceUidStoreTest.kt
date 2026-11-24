package com.simpleerp.mobile.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * `device_uid`「只生成一次」的并发测试（2026-11 审计 A11）。
 *
 * 事故形状：原来是「读 → 生成 → 写」三步，没有锁。两个协程同时进来（配对按钮连点、
 * 或配对与附带的 bootstrap 撞车），两边都读到空、各自生成不同的 uid 再各自写一遍 ——
 * 电脑端「已配对设备」里就长出**两条**同一台手机的记录。uid 机制存在的意义
 * 正是消灭这种重复，所以这个用例必须真的并发跑一遍，不能只测「正常路径返回一个值」。
 *
 * 为什么能跑在 JVM 上：`ServerConfig` 需要 DataStore（起不来），但「读-判-写」
 * 这段逻辑被抽成了 [DeviceUidStore]，读写通过回调注入，测试里换成内存变量即可。
 */
class DeviceUidStoreTest {

    /** 16 个协程同时要 uid：只允许生成一次，且所有人拿到同一个值。 */
    @Test
    fun testConcurrentCallersGenerateExactlyOneUid() = runBlocking {
        val store = DeviceUidStore()
        val persisted = AtomicReference("")
        val generateCount = AtomicInteger(0)

        val results = (1..16).map {
            async(Dispatchers.Default) {
                store.ensure(
                    existing = { persisted.get() },
                    store = { value -> persisted.set(value) },
                    generate = {
                        val n = generateCount.incrementAndGet()
                        // 加一点随机性：每次生成的值必须不同，否则「都拿到同一个值」
                        // 可能只是碰巧相同，测不出覆盖问题。
                        "uid-$n-${System.nanoTime()}"
                    },
                )
            }
        }.awaitAll()

        assertEquals("只允许生成一次", 1, generateCount.get())
        assertEquals("所有调用方必须拿到同一个 uid", 1, results.toSet().size)
        assertEquals(results.first(), persisted.get())
        assertTrue(results.first().isNotEmpty())
    }

    /** 已经有 uid 时直接复用，绝不重新生成（换了 uid = 服务端当成新设备）。 */
    @Test
    fun testExistingUidIsReusedWithoutGenerating() = runBlocking {
        val store = DeviceUidStore()
        val persisted = AtomicReference("already-there")
        val generateCount = AtomicInteger(0)

        val value = store.ensure(
            existing = { persisted.get() },
            store = { v -> persisted.set(v) },
            generate = {
                generateCount.incrementAndGet()
                "should-not-happen"
            },
        )

        assertEquals("already-there", value)
        assertEquals(0, generateCount.get())
    }

    /** 生成的 uid 形态：16 位小写十六进制（电脑端设备列表里要能肉眼核对）。 */
    @Test
    fun testGeneratedUidShape() {
        val uid = DeviceUidStore.generateDeviceUid()
        assertEquals(16, uid.length)
        assertTrue("不该带短横线：$uid", uid.none { it == '-' })
        assertTrue("只该是十六进制：$uid", uid.all { it in "0123456789abcdef" })
        // 两次生成必须不同（同一个手机不能被当成两台）。
        assertTrue(uid != DeviceUidStore.generateDeviceUid())
    }
}
