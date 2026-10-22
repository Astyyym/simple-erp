package com.simpleerp.mobile.net

import android.content.Context
import com.simpleerp.mobile.data.API_SCHEMA
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * 离线缓存：把每次成功拉到的 JSON 落盘，连不上时回落到它。
 *
 * 为什么这是原生端相对 WebView 壳的**真正收益**（不是「更好看」）：
 *
 * 服务端渲染的多页应用里，页面是 HTML，缓存它意味着缓存一份「看起来像数据」的
 * 文档 —— 时间戳、筛选态、分页全混在里面，过期了也没法只更新一半。
 * 这里缓存的是**结构化 JSON**，可以按接口粒度判断新鲜度，也能明确标注
 * 「数据截至 X 时」，而不会让用户以为看到的是实时数据。
 *
 * 三条硬规则：
 *
 * 1. **缓存键带 `schema`**：服务端字段一改，旧缓存自然读不到，不会解析崩溃。
 * 2. **只缓存成功响应**：把 500 或空响应当缓存，等于把故障永久化。
 * 3. **回落时必须能说出时间**：`CachedEntry.fetchedAt` 一路带到 UI，页面上
 *    明确显示「离线数据 · 截至 10月21日 13:05」。不标注的旧数据比没有数据更危险。
 */
class ResponseCache(private val context: Context) {

    private val root: File by lazy {
        File(context.filesDir, "api-cache").apply { mkdirs() }
    }

    private fun fileFor(key: String): File {
        // 键里可能有 `/` `?` `&` 等非法字符，统一哈希成文件名。
        val safe = key.hashCode().toUInt().toString(16)
        return File(root, "v${API_SCHEMA}_$safe.json")
    }

    suspend fun put(key: String, body: String, fetchedAt: Long = System.currentTimeMillis()) =
        withContext(Dispatchers.IO) {
            runCatching {
                val payload = fetchedAt.toString() + "\n" + body
                fileFor(key).writeText(payload, Charsets.UTF_8)
            }
        }

    suspend fun get(key: String): CachedEntry? = withContext(Dispatchers.IO) {
        runCatching {
            val file = fileFor(key)
            if (!file.exists()) return@runCatching null
            val text = file.readText(Charsets.UTF_8)
            val newline = text.indexOf('\n')
            if (newline <= 0) return@runCatching null
            val stamp = text.substring(0, newline).toLongOrNull() ?: return@runCatching null
            val body = text.substring(newline + 1)
            if (body.isBlank()) return@runCatching null
            CachedEntry(body, stamp)
        }.getOrNull()
    }

    suspend fun clear() = withContext(Dispatchers.IO) {
        runCatching { root.listFiles()?.forEach { it.delete() } }
    }

    /** 缓存里最新的一条时间戳（设置页展示「离线数据截至」用）。 */
    suspend fun newestTimestamp(): Long? = withContext(Dispatchers.IO) {
        runCatching {
            root.listFiles()
                ?.mapNotNull { file ->
                    file.readText(Charsets.UTF_8).substringBefore('\n').toLongOrNull()
                }
                ?.maxOrNull()
        }.getOrNull()
    }
}

data class CachedEntry(val body: String, val fetchedAt: Long)

/** 一份「可能是缓存」的数据：`fromCache` 为真时 UI 必须显示时间。 */
data class Loaded<T>(
    val value: T,
    val fromCache: Boolean = false,
    val fetchedAt: Long = 0L,
)

/**
 * 统一的「先网络、失败回落缓存」流程。
 *
 * 只有 `Unreachable`（连不上/超时）才回落 —— 服务端明确返回错误时必须显示错误，
 * 否则用户会拿着昨天的数据去报价，而界面看不出异常。
 */
suspend fun <T> ApiClient.loadWithCache(
    cache: ResponseCache,
    cacheKey: String,
    path: String,
    query: Map<String, String> = emptyMap(),
    decode: (String) -> T?,
): LoadOutcome<T> {
    return when (val result = get(path, query)) {
        is ApiResult.Success -> {
            val value = decode(result.body)
            if (value == null) {
                LoadOutcome.Error("服务器返回的数据格式不对（可能版本不匹配）。")
            } else {
                cache.put(cacheKey, result.body)
                LoadOutcome.Ok(Loaded(value, fromCache = false, fetchedAt = System.currentTimeMillis()))
            }
        }
        is ApiResult.Unreachable -> {
            val cached = cache.get(cacheKey)
            // decode 的返回类型是 T?，再包一层 Loaded 会被推断成 Loaded<T?>；
            // 显式判空后再构造，让编译器收窄到 T。
            if (cached != null) {
                val value = decode(cached.body)
                if (value != null) {
                    LoadOutcome.Ok(
                        Loaded(value, fromCache = true, fetchedAt = cached.fetchedAt)
                    )
                } else {
                    LoadOutcome.Error("连不上电脑。请确认电脑开着、手机和电脑连同一个 WiFi。")
                }
            } else {
                LoadOutcome.Error("连不上电脑。请确认电脑开着、手机和电脑连同一个 WiFi。")
            }
        }
        is ApiResult.Failure -> LoadOutcome.Error(result.message)
        is ApiResult.Unauthorized -> LoadOutcome.NeedsPairing
        is ApiResult.NoServerConfigured -> LoadOutcome.NeedsSetup
    }
}

sealed class LoadOutcome<out T> {
    data class Ok<T>(val data: Loaded<T>) : LoadOutcome<T>()
    data class Error(val message: String) : LoadOutcome<Nothing>()
    data object NeedsPairing : LoadOutcome<Nothing>()
    data object NeedsSetup : LoadOutcome<Nothing>()
}
