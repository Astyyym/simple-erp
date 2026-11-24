package com.simpleerp.mobile.net

import android.content.Context
import android.util.Log
import android.webkit.CookieManager
import com.simpleerp.mobile.data.API_SCHEMA
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * `/m/api/` 下各接口的客户端。
 *
 * 三件事必须在这里做对：
 *
 * 1. **cookie 自己管**。配对成功后服务端下发 `erp_mobile` cookie，原生端没有浏览器
 *    帮我们存，所以用 OkHttp 的 `CookieJar` 内存持有 + 手动落盘到 SharedPreferences，
 *    冷启动后仍是配对态（否则每次开 App 都要重新扫码输码）。
 * 2. **超时要短**。局域网正常请求 100ms 内返回；10 秒还连不上就是电脑没开或不同 WiFi，
 *    继续转圈只会让用户以为卡死。宁可快速失败并显示离线缓存。
 * 3. **区分「连不上」和「服务端报错」**。前者可以回落到缓存，后者必须把错误显示出来，
 *    不能拿旧数据盖住一个真实的 500。
 */
class ApiClient(private val context: Context) {

    private val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
        explicitNulls = false
    }

    /**
     * 对外暴露同一个实例：换地址时要清 cookie，如果外部另建一个
     * `PersistentCookieStore`，清的是另一个对象的内存副本，真正发请求的那个
     * 仍然带着旧 cookie —— 换了电脑却还用旧 token，表现是「一直 401 但重配对也没用」。
     */
    val cookieStore = PersistentCookieStore(context)

    /*
     * 超时按**局域网**定，不是按公网定。
     *
     * 原来 connectTimeout 是 8 秒（2026-10-22 实测电脑关机时切页面要干等 8.7 秒）。
     * 但这是同一台路由器下的手机连电脑：正常情况是 20~80ms，
     * 超过 2.5 秒基本只有两种可能 —— 电脑关机了、或者手机不在这个网里。
     * 两种情况都不会因为多等 5 秒而变好，只会让用户盯着转圈。
     *
     * readTimeout 15 → 30 秒（2026-10-31）：那是「已经连上了、服务端在算」的等待。
     * 快照范围放大后（单据详情 400 / 客户详情全量 / 商品详情全量），门店那台电脑
     * 生成一次可能要 5~10 秒 —— 15 秒会把「正在慢慢算」误判成「连不上」，
     * 然后回落到旧缓存，用户看到的是「同步了但数据没变」。
     */
    private val client = OkHttpClient.Builder()
        // 1.5 秒（2026-10-22 从 2.5 秒再收紧）。
        //
        // 用户反馈「断网时二级页面能开，但加载时间有点长」。缓存是瞬间出的，
        // 那点延迟**全是这个 connectTimeout** —— 断网时要干等它放弃才回落。
        //
        // 局域网正常是 20~80ms，1.5 秒已经是很宽松的余量：真超过就说明
        // 电脑关了或者不在同一个网，多等一秒也不会变好。
        .connectTimeout(1500, TimeUnit.MILLISECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .cookieJar(cookieStore)
        .build()

    var baseUrl: String = ""
        private set

    fun updateBaseUrl(value: String) {
        baseUrl = value.trimEnd('/')
    }

    suspend fun get(path: String, query: Map<String, String> = emptyMap()): ApiResult =
        withContext(Dispatchers.IO) {
            val root = baseUrl
            if (root.isEmpty()) return@withContext ApiResult.NoServerConfigured
            val url = buildUrl(root, path, query) ?: return@withContext ApiResult.NoServerConfigured
            val request = Request.Builder().url(url).get().build()
            execute(request)
        }

    suspend fun postJson(path: String, body: String): ApiResult = withContext(Dispatchers.IO) {
        val root = baseUrl
        if (root.isEmpty()) return@withContext ApiResult.NoServerConfigured
        val url = buildUrl(root, path, emptyMap()) ?: return@withContext ApiResult.NoServerConfigured
        val request = Request.Builder()
            .url(url)
            .post(body.toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()
        execute(request)
    }

    private fun buildUrl(root: String, path: String, query: Map<String, String>): HttpUrl? {
        val full = root + path
        val parsed = full.toHttpUrlOrNull() ?: return null
        if (query.isEmpty()) return parsed
        val builder = parsed.newBuilder()
        query.forEach { (key, value) -> builder.addQueryParameter(key, value) }
        return builder.build()
    }

    private fun execute(request: Request): ApiResult {
        return try {
            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                val code = response.code
                when {
                    code == 401 -> ApiResult.Unauthorized
                    !response.isSuccessful -> ApiResult.Failure(code, extractError(text, code))
                    text.isBlank() -> ApiResult.Failure(code, "服务器返回了空内容。")
                    else -> ApiResult.Success(text)
                }
            }
        } catch (e: Exception) {
            // 连不上 / 超时 / DNS 失败：都归到「服务器不可达」，允许回落缓存。
            ApiResult.Unreachable(e.message ?: "无法连接服务器")
        }
    }

    /**
     * 从错误响应里取 `error` 文案。
     *
     * **必须走 JSON 解析，不能用正则抠原文**：Flask 的 `jsonify` 默认把中文转成
     * `\uXXXX` 转义，正则抓到的是转义串本身（真机实测：界面上直接显示了
     * `\u914d\u5bf9\u7801...`）。解析成 JsonObject 再取 `jsonPrimitive.content`
     * 才会还原成中文。
     */
    private fun extractError(body: String, code: Int): String {
        val parsed = runCatching {
            val element = json.parseToJsonElement(body)
            val message = element.jsonObject["error"]
            message?.jsonPrimitive?.contentOrNull
        }.getOrNull()
        if (!parsed.isNullOrBlank()) return parsed
        return "请求失败（HTTP $code）"
    }

    /**
     * 解析响应体。
     *
     * 注意：**不能用 `inline fun <reified T>`** —— inline 函数无法访问 `private` 的
     * `json` 实例（Kotlin 会报 "public-API inline function cannot access
     * non-public-API"）。改成接收 `KSerializer` 的普通函数，调用处传
     * `Serializer()`，同样类型安全且没有反射开销。
     */
    fun <T> decode(text: String, serializer: kotlinx.serialization.DeserializationStrategy<T>): T? =
        runCatching { json.decodeFromString(serializer, text) }.getOrNull()

    fun schema(): Int = API_SCHEMA
}

/** 网络结果：把「连不上」与「服务端说不行」分开，缓存策略依赖这个区分。 */
sealed class ApiResult {
    data class Success(val body: String) : ApiResult()
    data class Failure(val code: Int, val message: String) : ApiResult()
    data object Unauthorized : ApiResult()
    data class Unreachable(val message: String) : ApiResult()
    data object NoServerConfigured : ApiResult()
}

/** 原生端唯一的日志 tag（`adb logcat -s SimpleERP`）。 */
private const val LOG_TAG = "SimpleERP"

/**
 * cookie 持久化这条链路上**唯一**的日志出口。
 *
 * 为什么只有这里有日志（2026-11 审计 A7）：cookie 落盘是「静默丢失即可毁掉
 * 一次配对」的地方，读不出来时用户只看到「又要重新配对」，其它地方靠界面
 * 已经把结论说清楚了，不需要再刷日志。
 *
 * 为什么裹一层 `runCatching`：JVM 单元测试（`CookiePersistenceTest`）里没有
 * Android 运行时，`Log.w` 会直接抛 `RuntimeException: not mocked` ——
 * 一行日志不能把键格式的回归测试带崩。日志本身失败不影响任何业务结果。
 */
internal fun cookieWarn(message: String) {
    runCatching { Log.w(LOG_TAG, message) }
}

/**
 * cookie 落盘的键与编解码（纯函数，可被 JVM 单测覆盖）。
 *
 * **一条 cookie 一个键**：`host:port|cookieName`。
 *
 * ⚠️ 这一层踩过真实事故（2026-11-24 用户报「清掉手机后台再打开 App 又要重新配对」）。
 * 原来一台主机只写**一个**键 `host:port`，而配对成功时服务端一次下发两个 cookie
 * （`erp_mobile` 认证 token + `erp_device_uid` 设备身份）。`putString` 同桌覆盖，
 * 后写的 uid 把 token 顶掉了。而进程活着时内存里两个都在 —— 所以当场用得好好的，
 * **只有进程被清掉再打开才暴露**：冷启动只读回 uid、没有 token，`/m/api/meta` 401，
 * 启动路由判定 NOT_PAIRED → 弹出配对页。表现正是「其它都对，就是清后台后要重配」。
 */
internal object CookiePersistence {

    /** 主机键与 cookie 名的分隔符。cookie 名（RFC 6265 token）里不可能出现它。 */
    const val SEPARATOR = '|'

    /** 落盘的「主机键」：`host:port`，不含协议。 */
    fun hostKey(url: HttpUrl): String = url.host + ":" + url.port

    /**
     * 从**地址串**取主机键（`http://192.168.10.5:5001` → `192.168.10.5:5001`）。
     *
     * 换地址时要按「旧主机」清 cookie / 缓存（A6/A4），而那时手上只有
     * 一个已经存下来的字符串，不是一个 `HttpUrl`。解析不出来返回 null
     * （脏地址不该把清理流程弄崩，调用方按「没有旧主机」处理）。
     */
    fun hostKeyOf(baseUrl: String): String? =
        baseUrl.trim().toHttpUrlOrNull()?.let { hostKey(it) }

    /** 单条 cookie 的落盘键。 */
    fun entryKey(hostKey: String, cookieName: String): String = "$hostKey$SEPARATOR$cookieName"

    /**
     * 一条落盘记录 → `(主机键, cookie)`；认不出来返回 null（脏数据不该把配对态读崩）。
     *
     * 键有两种形态：新格式 `host:port|name`，和老版本只有 `host:port` 的。
     * `Cookie.parse` 需要一个完整 URL，而局域网只有 http，补 `http://` 即可。
     */
    fun decodeEntry(entryKey: String, rawValue: String): Pair<String, Cookie>? {
        val cut = entryKey.lastIndexOf(SEPARATOR)
        val hostKey = if (cut > 0) entryKey.substring(0, cut) else entryKey
        val url = ("http://" + hostKey).toHttpUrlOrNull()
        if (url == null) {
            // 静默跳过是 2026-11 审计挖出来的问题：配对态读不出来时，
            // 用户只看到「又要重新配对」，日志里 0 行线索，没人能查。
            // 键本身可能带 cookie 值以外的信息，但主机名不是秘密，直接打出来。
            cookieWarn("cookie 落盘键认不出主机，已跳过：$entryKey")
            return null
        }
        val cookie = Cookie.parse(url, rawValue)
        if (cookie == null) {
            cookieWarn("cookie 内容解析失败，已跳过：$entryKey")
            return null
        }
        return hostKey to cookie
    }

    /**
     * 落盘记录里**已经过期**的那些键（磁盘上要删掉的）。
     *
     * 为什么必须删盘：`loadForRequest` 只从内存删过期 cookie，磁盘那份从来不删，
     * 于是一次进程重启（清后台、系统回收）又把它全读回内存 —— 服务端早已作废的
     * token 会一直躺在那里，看起来「配过对」，实际每次请求都 401（A5）。
     */
    fun expiredEntryKeys(
        entries: Map<String, String>,
        now: Long = System.currentTimeMillis(),
    ): List<String> = entries.entries.mapNotNull { (rawKey, rawValue) ->
        val (_, cookie) = decodeEntry(rawKey, rawValue) ?: return@mapNotNull null
        if (cookie.expiresAt < now) rawKey else null
    }

    /** 内存里的 cookie 列表滤掉过期的（与 [expiredEntryKeys] 同一口径）。 */
    fun pruneExpired(cookies: List<Cookie>, now: Long = System.currentTimeMillis()): List<Cookie> =
        cookies.filter { it.expiresAt >= now }

    /**
     * 「某台主机」在落盘里的所有键：新格式 `host:port|name` + 老格式 `host:port`。
     *
     * 换地址时按主机清（A6），不能一刀 `clear()`：那是「断开连接」的语义 ——
     * 把另一家店/另一台电脑的配对态也一起抹了。
     *
     * 前缀比较用 `hostKey + SEPARATOR`，不能用 `startsWith(hostKey)`：
     * `192.168.10.5:5001` 是 `192.168.10.5:50011` 的前缀，后者是另一台电脑。
     */
    fun entryKeysForHost(keys: Collection<String>, hostKey: String): List<String> =
        keys.filter { it == hostKey || it.startsWith("$hostKey$SEPARATOR") }

    /**
     * 全部落盘记录 → `主机键 → cookie 列表`。
     *
     * 老格式先处理、新格式后处理：同一台主机同时存在两种键时新格式胜出，
     * 不会读回两条同名 cookie（升级过渡期只有这一种交错情况）。
     */
    fun decodeAll(entries: Map<String, String>): Map<String, List<Cookie>> {
        val out = mutableMapOf<String, MutableList<Cookie>>()
        entries.entries
            .sortedBy { it.key.indexOf(SEPARATOR) >= 0 }  // false(老格式) 在前
            .forEach { (rawKey, rawValue) ->
                val (hostKey, cookie) = decodeEntry(rawKey, rawValue) ?: return@forEach
                val list = out.getOrPut(hostKey) { mutableListOf() }
                list.removeAll { it.name == cookie.name }
                list.add(cookie)
            }
        return out
    }
}

/**
 * cookie 持久化：内存 CookieJar + SharedPreferences 落盘。
 *
 * 为什么要手写：OkHttp 自带的是内存 CookieJar，进程一死就丢；而服务端的会话
 * 就是 cookie 本身（没有 refresh token），丢了就得重新配对。
 *
 * 键格式与那次「清后台就要重新配对」的事故见 [CookiePersistence]。
 */
class PersistentCookieStore(context: Context) : CookieJar {

    private val prefs = context.getSharedPreferences("simple_erp_cookies", Context.MODE_PRIVATE)
    private val cache = mutableMapOf<String, MutableList<Cookie>>()

    init {
        // 载入委托给纯函数 [CookiePersistence.decodeAll]，键格式的坑见那边的说明。
        val stored = prefs.all
            .mapNotNull { (key, value) -> (value as? String)?.let { key to it } }
            .toMap()
        // 冷启动先把磁盘上过期的删掉，再把剩下的读进内存（A5）。
        //
        // 原来这里无条件全装回内存：`loadForRequest` 只在**内存**里删过期项，
        // 磁盘那份永远留着，于是每次进程重启都把服务端早已作废的 token 捞回来 ——
        // 表现是「明明配对过、却每次请求都 401」，而且怎么清内存都没用。
        // 这里顺手把过期键从磁盘删掉，内存里也只放没过期的，两条链路口径一致。
        val now = System.currentTimeMillis()
        val staleKeys = CookiePersistence.expiredEntryKeys(stored, now)
        if (staleKeys.isNotEmpty()) {
            val editor = prefs.edit()
            staleKeys.forEach { editor.remove(it) }
            // 这里用 `apply()` 而不是 `commit()`：`init` 是在 ViewModel 构造时跑的
            // （主线程），同步落盘会卡启动。而且**语义上也安全** —— 万一这次异步写没落地，
            // 下次冷启动还是会把同样的键判成过期再删一遍，内存里也始终过滤着它们。
            editor.apply()
            cookieWarn("冷启动清掉 ${staleKeys.size} 条过期 cookie：$staleKeys")
        }
        CookiePersistence.decodeAll(stored - staleKeys.toSet()).forEach { (hostKey, cookies) ->
            val alive = CookiePersistence.pruneExpired(cookies, now)
            if (alive.isNotEmpty()) cache[hostKey] = alive.toMutableList()
        }
    }

    @Synchronized
    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        val key = CookiePersistence.hostKey(url)
        val list = cache.getOrPut(key) { mutableListOf() }
        val editor = prefs.edit()
        // 老版本「一台主机一条」的键：升级后第一次保存就清掉，不然它会被一直读回来。
        editor.remove(key)
        val now = System.currentTimeMillis()
        cookies.forEach { cookie ->
            list.removeAll { it.name == cookie.name }
            val entryKey = CookiePersistence.entryKey(key, cookie.name)
            if (cookie.expiresAt < now) {
                // 服务端删掉了这个 cookie（过期即删）：落盘那份也要删，
                // 否则冷启动又把它读回来，还会被当成有效的身份。
                editor.remove(entryKey)
            } else {
                list.add(cookie)
                editor.putString(entryKey, cookie.toString())
            }
        }
        // `commit()` 而不是 `apply()`（A8）：配对响应一到就落盘，而用户点完「配对」
        // 很可能立刻划掉后台 —— `apply()` 是异步写，进程被清掉时那一次写可能还没落盘，
        // 下次冷启动就没有 token，又得重新配对（正是 2026-11-24 那类事故的加固）。
        // 调用点在 OkHttp 的工作线程上（`saveFromResponse`），不会卡主线程。
        if (!editor.commit()) cookieWarn("cookie 落盘失败（commit 返回 false）：$key")
    }

    @Synchronized
    override fun loadForRequest(url: HttpUrl): List<Cookie> {
        val key = CookiePersistence.hostKey(url)
        val now = System.currentTimeMillis()
        val list = cache[key] ?: return emptyList()
        // 内存里删过期项的同时，磁盘那份也要删（A5）：只删内存的话，
        // 下一次冷启动会把这条已经作废的 cookie 重新装回来，看起来像「配过对」，
        // 实际每次请求都 401。
        val expired = list.filter { it.expiresAt < now }
        if (expired.isNotEmpty()) {
            list.removeAll(expired)
            val editor = prefs.edit()
            expired.forEach { editor.remove(CookiePersistence.entryKey(key, it.name)) }
            if (!editor.commit()) cookieWarn("过期 cookie 清理落盘失败：$key")
        }
        return list.toList()
    }

    /**
     * 清 cookie。[hostKey] 为空 = 全清（「断开连接」用），传了就只清那台主机（A6）。
     *
     * 为什么要按主机清：粒度必须和落盘一致（一条 cookie 一个 `host:port|name` 键）。
     * 「换地址」是「不再连这台电脑」，把另一家店的配对态一起抹掉没有任何道理 ——
     * 用户换回原地址时还得重新配对一次。
     *
     * 按主机清时**不动** `CookieManager`：它的 API 只有「全清」，没有「清某个主机」，
     * 而且这个 App 里没有任何 WebView（请求全走 OkHttp），那里的 cookie 本来就是空的。
     * 全清分支仍然清一遍，是因为「断开连接」要保证本机不留任何残留凭据。
     */
    @Synchronized
    fun clear(hostKey: String? = null) {
        val stored = prefs.all
            .mapNotNull { (key, value) -> (value as? String)?.let { key to it } }
            .toMap()
        if (hostKey == null) {
            cache.clear()
            prefs.edit().clear().apply()
            CookieManager.getInstance().removeAllCookies(null)
            CookieManager.getInstance().flush()
            return
        }
        cache.remove(hostKey)
        // 两种键格式都要删：老格式 `host:port` 与新格式 `host:port|name`。
        val keys = CookiePersistence.entryKeysForHost(stored.keys, hostKey)
        if (keys.isEmpty()) return
        val editor = prefs.edit()
        keys.forEach { editor.remove(it) }
        // 换地址是紧接着要重新配对的动作，同步落盘才不会「清完又被下一次冷启动读回来」。
        if (!editor.commit()) cookieWarn("按主机清 cookie 落盘失败：$hostKey")
    }
}
