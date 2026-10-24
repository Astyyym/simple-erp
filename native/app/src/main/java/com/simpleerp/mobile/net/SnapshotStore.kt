package com.simpleerp.mobile.net

import android.content.Context
import com.simpleerp.mobile.data.API_SCHEMA
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File

/**
 * 全量快照缓存：`GET /m/api/snapshot` 的落盘结果（2026-10-24）。
 *
 * 为什么在 [ResponseCache] 之外**再加一层**，而不是把快照拆进同一堆按接口存的文件：
 *
 * 1. **解析一次就够**。快照是一个 JSON，拆成 N 个文件意味着「同步」要做 N 次写盘 +
 *    N 次小文件 IO；一次性写一个大文件更快，也不会留下「写了一半」的中间态
 *    （整个文件原子替换，要么全是这次的，要么全是上次的）。
 * 2. **缓存键必须由服务端给**。快照里的键是服务端算好的规范化 path
 *    （`/m/api/orders?date_mode=month&month=10&year=2026`）。客户端只照抄，
 *    不自己拼 query —— 两端各拼一套就等着「在线能看、离线查不到」。
 * 3. **能说出时间**。整份快照共用一个 `syncedAt`，离线横幅显示的就是它，
 *    而不是每一页各自的时间（那样一个页面会显示「3 分钟前」、另一个「昨天」）。
 *
 * 与 `ResponseCache` 的关系是**兜底层**：精确键（`ResponseCache`）优先，
 * 没命中才查快照。精确键是「用户真的打开过这一页」的证据，更新；快照是「同步过」，
 * 覆盖广但可能稍旧。
 */
class SnapshotStore(private val context: Context) {

    private val root: File by lazy {
        File(context.filesDir, "snapshot-cache").apply { mkdirs() }
    }

    private val json = Json { ignoreUnknownKeys = true }

    private fun fileFor(): File = File(root, "v${API_SCHEMA}.json")

    @Serializable
    private data class Payload(
        val syncedAt: Long = 0L,
        val entries: Map<String, String> = emptyMap(),
    )

    /** 落盘：整份替换（先写临时文件再 rename，避免半截 JSON）。 */
    suspend fun save(syncedAt: Long, entries: Map<String, String>) = withContext(Dispatchers.IO) {
        runCatching {
            val body = json.encodeToString(
                Payload.serializer(),
                Payload(syncedAt = syncedAt, entries = entries),
            )
            val target = fileFor()
            val temp = File(root, target.name + ".tmp")
            temp.writeText(body, Charsets.UTF_8)
            if (target.exists()) target.delete()
            temp.renameTo(target)
        }
    }

    private suspend fun load(): Payload? = withContext(Dispatchers.IO) {
        runCatching {
            val file = fileFor()
            if (!file.exists()) return@runCatching null
            json.decodeFromString(Payload.serializer(), file.readText(Charsets.UTF_8))
        }.getOrNull()
    }

    /**
     * 按**服务端给的规范化 path** 查快照。
     *
     * 调用方必须传「与快照键完全一致」的串。这里提供一个 [canonicalKey] 帮助函数，
     * 规则与 Python 侧 `_canonical_path` 逐字对齐：query 键按字母序、跳过空值。
     */
    suspend fun get(key: String): CachedEntry? {
        val payload = load() ?: return null
        val body = payload.entries[key] ?: return null
        if (body.isBlank()) return null
        return CachedEntry(body, payload.syncedAt)
    }

    suspend fun syncedAt(): Long = load()?.syncedAt ?: 0L

    suspend fun entryCount(): Int = load()?.entries?.size ?: 0

    suspend fun clear() = withContext(Dispatchers.IO) {
        runCatching { fileFor().delete() }
    }

    companion object {
        /**
         * 与 Python `mobile_api._canonical_path` 同一规则：
         * `path?k=v&k2=v2`，键按字母序，值为空串的参数**跳过**。
         *
         * 为什么必须两端一致：这个串是缓存键。客户端多拼一个 `start_date=`（空值）
         * 就会与快照里的键错开，表现是「断网时这一档永远空白」。
         */
        fun canonicalKey(path: String, query: Map<String, String>): String {
            val pairs = query.entries
                .filter { it.value.isNotEmpty() }
                .sortedBy { it.key }
            if (pairs.isEmpty()) return path
            return path + "?" + pairs.joinToString("&") { "${it.key}=${it.value}" }
        }
    }
}
