package com.simpleerp.mobile.net

import android.content.Context
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
     * readTimeout 保持 15 秒：那是「已经连上了、服务端在算」的等待，
     * 大数据量（全部时间的单据）确实需要时间，砍短会误判成失败。
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
        .readTimeout(15, TimeUnit.SECONDS)
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

/**
 * cookie 持久化：内存 CookieJar + SharedPreferences 落盘。
 *
 * 为什么要手写：OkHttp 自带的是内存 CookieJar，进程一死就丢；而服务端的会话
 * 就是 cookie 本身（没有 refresh token），丢了就得重新配对。
 */
class PersistentCookieStore(context: Context) : CookieJar {

    private val prefs = context.getSharedPreferences("simple_erp_cookies", Context.MODE_PRIVATE)
    private val cache = mutableMapOf<String, MutableList<Cookie>>()

    init {
        prefs.all.forEach { (key, value) ->
            if (value !is String) return@forEach
            // 落盘的键是 `host:port`（不含协议），而 `Cookie.parse` 要求一个完整 URL。
            // 局域网只有 http，这里补 `http://` 即可；补不出来就跳过这条脏数据，
            // 不让一条坏 cookie 把整个配对态读崩。
            val url = ("http://" + key).toHttpUrlOrNull() ?: return@forEach
            val cookie = Cookie.parse(url, value) ?: return@forEach
            cache.getOrPut(key) { mutableListOf() }.add(cookie)
        }
    }

    @Synchronized
    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        val key = url.host + ":" + url.port
        val list = cache.getOrPut(key) { mutableListOf() }
        cookies.forEach { cookie ->
            list.removeAll { it.name == cookie.name }
            list.add(cookie)
            prefs.edit().putString(key, cookie.toString()).apply()
        }
    }

    @Synchronized
    override fun loadForRequest(url: HttpUrl): List<Cookie> {
        val key = url.host + ":" + url.port
        val now = System.currentTimeMillis()
        val list = cache[key] ?: return emptyList()
        list.removeAll { it.expiresAt < now }
        return list.toList()
    }

    @Synchronized
    fun clear() {
        cache.clear()
        prefs.edit().clear().apply()
        CookieManager.getInstance().removeAllCookies(null)
        CookieManager.getInstance().flush()
    }
}
