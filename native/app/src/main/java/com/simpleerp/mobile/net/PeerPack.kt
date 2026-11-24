package com.simpleerp.mobile.net

import com.simpleerp.mobile.data.API_SCHEMA
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 同伴数据包：把「这台手机同步到的快照」打成一个文件，交给另一台手机导入。
 *
 * 为什么需要它（2026-11-21 用户需求）：A、B 是同一套账，手机端本身**纯只读**
 * （除 `/pair` 外全是 GET），所以 A 手机里没有「B 缺的数据」这种东西 ——
 * 它只是店里那台电脑某一刻的镜像。B 缺的不是数据合并能力，是**那份镜像本身**。
 * 因此不需要同步引擎，只需要把 `SnapshotStore` 那个 JSON 搬过去。
 *
 * 关键约束：
 * 1. **不带凭据**：`baseUrl` 可以带（B 从没配置过地址时兜底），cookie / device token 绝不带。
 * 2. **必须带 schema**：`entries` 的键是服务端算好的规范化 path，`API_SCHEMA` 一变键就全对不上，
 *    所以导入前要硬校验，宁可直接拒绝也不要"合进去看起来能用"。
 * 3. **必须带 syncedAt**：这是这份数据的**截止时刻**，不是"现在"。界面必须显示它，
 *    否则 B 会以为看到的是实时数据 —— 这是本方案唯一真正的风险。
 */
@Serializable
data class PeerPack(
    val kind: String = KIND,
    val packVersion: Int = PACK_VERSION,
    /** 生成这份包的 App 的 [API_SCHEMA]，导入端必须一致。 */
    val apiSchema: Int = API_SCHEMA,
    val appVersion: String = "",
    /** 店名，纯展示：B 设置页要能说出"这份数据来自哪家店"。 */
    val shopName: String = "",
    /** A 手机当时连的电脑地址。只在 B 从未配置过地址时用来兜底。 */
    val baseUrl: String = "",
    /** 数据截止时刻（epoch 毫秒）= 服务端快照的 syncedAt。 */
    val syncedAt: Long = 0L,
    val syncMonths: Int = ServerConfig.DEFAULT_SYNC_MONTHS,
    val syncFrom: String = "",
    val syncTo: String = "",
    @SerialName("backup_key") val backupKey: String = "",
    /** 规范化 path → 响应体。与 `SnapshotStore` 内部结构一致，可直接落盘。 */
    val entries: Map<String, String> = emptyMap(),
) {

    /**
     * 校验能否导入。返回 null 表示通过，否则返回给人看的拒绝原因。
     *
     * 顺序是有意的：先说"这不是我们的文件"，再说"版本不认识"，最后说"两端对不上"。
     * 三种失败对用户来说是三件不同的事，混成一句「导入失败」他没法处置。
     */
    fun rejection(): String? = when {
        kind != KIND ->
            "这不是同伴数据包（文件类型不对）。"
        packVersion != PACK_VERSION ->
            "数据包版本不认识（$packVersion），请两边都更新到同一个版本。"
        apiSchema != API_SCHEMA ->
            "版本对不上：数据包是 schema $apiSchema，本机是 $API_SCHEMA。请把两台手机都更新到同一版本。"
        syncedAt <= 0L ->
            "数据包里没有同步时间，可能是损坏的文件。"
        entries.isEmpty() ->
            "数据包里没有数据（可能是同步失败时生成的）。"
        else -> null
    }

    /** 条目数，用于导入完成提示。 */
    val entryCount: Int get() = entries.size

    companion object {
        const val KIND = "simple-erp-peer-pack"
        const val PACK_VERSION = 1
        const val MIME = "application/json"

        private val json = Json {
            ignoreUnknownKeys = true
            encodeDefaults = true
        }

        fun encode(pack: PeerPack): String = json.encodeToString(serializer(), pack)

        /** 解析并校验。返回 [Result]，失败时 `exceptionOrNull().message` 就是给人看的原因。 */
        fun decode(text: String): Result<PeerPack> = runCatching {
            val pack = json.decodeFromString(serializer(), text)
            pack.rejection()?.let { throw IllegalArgumentException(it) }
            pack
        }

        /**
         * 建议文件名：`简单ERP同伴数据_20261121_1730.json`。
         *
         * 带截止时间而不是生成时间——用户在微信里挑文件时，能分辨哪份更新，
         * 靠的就是文件名，而不是点开看。
         */
        fun suggestFileName(syncedAt: Long): String {
            val fmt = SimpleDateFormat("yyyyMMdd_HHmm", Locale.US)
            return "简单ERP同伴数据_${fmt.format(Date(syncedAt))}.json"
        }
    }
}
