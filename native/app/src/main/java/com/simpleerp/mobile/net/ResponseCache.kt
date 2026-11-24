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
 *
 * 2026-10-24：回落链从「精确键 → 报错」扩展成「精确键 → 全量快照 → 报错」。
 * 为什么要第二层：用户实测「二级页面断网刷不出来」—— 详情页只在**点开那一刻**
 * 才落盘，所以从没点开过的单据断网必然空白。快照（`/m/api/snapshot`）在关闭电脑前
 * 一次把列表 + 详情都写下来，第二层就是给它用的。
 *
 * 顺序不能反：精确键是「用户真的打开过这一页」的证据，比快照新；先查它才对。
 */
suspend fun <T> ApiClient.loadWithCache(
    cache: ResponseCache,
    cacheKey: String,
    path: String,
    query: Map<String, String> = emptyMap(),
    decode: (String) -> T?,
    /** 全量快照：第二层回落。旧调用方不传也能工作（就是原来的行为）。 */
    snapshot: SnapshotStore? = null,
    /**
     * 「此刻这台手机本来就该按离线用」（同伴导入的数据 / 用户主动选了看缓存）。
     *
     * 为真时 401 与「连不上」走同一条回落链（A3）：B 的手机上有一份完整快照，
     * 但它从没和这台电脑配对过，每个请求都 401 —— 如果照常返回 `NeedsPairing`，
     * 刚导入的数据会被一个全屏配对页顶掉，人什么都看不到。
     */
    offlineAllowed: Boolean = false,
): LoadOutcome<T> {
    /**
     * 回落链：精确键 → 全量快照 → 「连不上」。
     *
     * 解不开的缓存**就当没有**（继续往下找），绝不反过来污染：
     * 一份坏数据（schema 变了、写了一半）不该把快照里那份能看的旧数据挡掉，
     * 也不该把「连不上」说成「数据格式不对」。
     */
    suspend fun fallback(): LoadOutcome<T> {
        val cached = cache.get(cacheKey)
        // decode 的返回类型是 T?，再包一层 Loaded 会被推断成 Loaded<T?>；
        // 显式判空后再构造，让编译器收窄到 T。
        if (cached != null) {
            val value = decode(cached.body)
            if (value != null) {
                return LoadOutcome.Ok(
                    Loaded(value, fromCache = true, fetchedAt = cached.fetchedAt)
                )
            }
        }
        // 第二层：全量快照。键必须用服务端那套规范化规则，否则查不到。
        val snap = snapshot?.get(SnapshotStore.canonicalKey(path, query))
        if (snap != null) {
            val value = decode(snap.body)
            if (value != null) {
                return LoadOutcome.Ok(
                    Loaded(value, fromCache = true, fetchedAt = snap.fetchedAt)
                )
            }
        }
        return LoadOutcome.Error(
            "连不上电脑。请确认电脑开着、手机和电脑连同一个 WiFi。",
            LoadOutcome.Error.Kind.OFFLINE,
        )
    }

    return when (val result = get(path, query)) {
        is ApiResult.Success -> {
            val value = decode(result.body)
            if (value == null) {
                // 服务端回的东西解不开：kind 标出来，调用方才能决定「是不是该保住
                // 屏幕上已经能看的那份旧数据」（见 `AppViewModel.load`，A12）。
                LoadOutcome.Error(
                    "服务器返回的数据格式不对（可能版本不匹配）。",
                    LoadOutcome.Error.Kind.BAD_PAYLOAD,
                )
            } else {
                cache.put(cacheKey, result.body)
                LoadOutcome.Ok(Loaded(value, fromCache = false, fetchedAt = System.currentTimeMillis()))
            }
        }
        is ApiResult.Unreachable -> fallback()
        // 401 = 服务端说「这台手机没被允许」。正常情况去配对页；
        // 「按离线用」时按连不上处理（A3），否则导入的数据会被配对页顶掉。
        is ApiResult.Unauthorized -> if (offlineAllowed) fallback() else LoadOutcome.NeedsPairing
        is ApiResult.Failure -> LoadOutcome.Error(result.message, LoadOutcome.Error.Kind.SERVER)
        is ApiResult.NoServerConfigured -> LoadOutcome.NeedsSetup
    }
}

sealed class LoadOutcome<out T> {
    data class Ok<T>(val data: Loaded<T>) : LoadOutcome<T>()

    /**
     * 失败，带一个 [Kind]：`AppViewModel` 要根据它区分「必须显示的错」与
     * 「可以拿旧数据顶上的错」。只靠 message 文本判断太脆（改一个字就静默失效）。
     */
    data class Error(
        val message: String,
        val kind: Kind = Kind.SERVER,
    ) : LoadOutcome<Nothing>() {
        enum class Kind {
            /** 服务端明确报错（4xx/5xx）：必须显示，不能拿旧数据盖住。 */
            SERVER,

            /** 服务端回了内容但解不开（版本不匹配 / 被中间页劫持）。 */
            BAD_PAYLOAD,

            /** 连不上（含「按离线处理」的 401）：可以回落旧数据。 */
            OFFLINE,
        }
    }

    data object NeedsPairing : LoadOutcome<Nothing>()
    data object NeedsSetup : LoadOutcome<Nothing>()
}
