package com.simpleerp.mobile.net

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "simple_erp_native")

/**
 * 服务器地址与配对态的持久化。
 *
 * **base_url 绝不编译进包里**：地址存在 DataStore，扫码或手输可改。
 * 局域网 IP 会漂移（架构文档 §2.4），编译进包意味着 IP 一变就得重新发版。
 */
class ServerConfig(private val context: Context) {

    private object Keys {
        val BASE_URL = stringPreferencesKey("base_url")
        val SHOP_NAME = stringPreferencesKey("shop_name")
        val DEVICE_UID = stringPreferencesKey("device_uid")
        val LAST_SYNC_AT = stringPreferencesKey("last_sync_at")

        /** 「这台手机确实和这台电脑配对成功过」——见 [pairedConfirmed] 的说明。 */
        val PAIRED_CONFIRMED = stringPreferencesKey("paired_confirmed")
    }

    /** 返回形如 `http://192.168.10.5:5001` 的根地址；未配置时为空串。 */
    suspend fun baseUrl(): String =
        context.dataStore.data.map { it[Keys.BASE_URL] ?: "" }.first()

    suspend fun saveBaseUrl(raw: String) {
        context.dataStore.edit { it[Keys.BASE_URL] = normalize(raw) }
    }

    /**
     * 「这台手机**确实**和这台电脑配对成功过」（2026-10-31）。
     *
     * 为什么需要它：原来只凭「`/m/api/meta` 连不上」就判断要不要进主界面，于是
     * **从没配对成功、只是网络不通**的手机也会被放进主界面看旧缓存（真实事故：
     * 门店电脑防火墙 Block 规则 → 手机永远连不上 → App 看起来「配好了」）。
     *
     * 有了这个标志，两种「连不上」就能分开：
     * - `true` + 连不上 → 电脑可能只是关机了，**照旧进主界面看缓存**（明确需求，别弄坏）；
     * - `false` + 连不上 → 停在「连不上电脑」页，不再伪装成已配对。
     *
     * 置 `true` 的唯一时机：服务端真的回过 `paired=true`，或配对提交成功。
     * 置 `false`：改地址（换电脑）、断开连接、服务端明确说没配对（401 / `paired=false`）。
     *
     * ⚠️ 它是**本地展示用**标志，不是凭据：清数据/重装只会让用户多确认一次配对，
     * 认证永远只有 cookie 里的 token（见 `mobile_pairing`）。
     */
    suspend fun pairedConfirmed(): Boolean =
        context.dataStore.data.map { it[Keys.PAIRED_CONFIRMED] == "1" }.first()

    suspend fun savePairedConfirmed(value: Boolean) {
        context.dataStore.edit {
            if (value) it[Keys.PAIRED_CONFIRMED] = "1" else it.remove(Keys.PAIRED_CONFIRMED)
        }
    }

    /**
     * 这台手机的**稳定身份**（2026-10-24）。
     *
     * 为什么要它：电脑端「已配对设备」里同一台手机重复配对会长出两条一样的记录
     * （实测：手机点「改地址」会清 cookie 再配一次，服务端每次都新增一条）。
     * 有了这个 uid，服务端就能把同一台设备的记录**原地更新**成一个。
     *
     * 三条约束：
     * 1. **生成一次、永不变**：配对、改地址、重新检测都不重写它。
     * 2. **不在 `clear()` 里删**：`clear()` 的语义是「不再连这台电脑」，
     *    不是「换一台手机」。删了它，下次连回来就又是一条新记录。
     * 3. 只是标签，不是凭据：服务端只拿它做合并展示，认证永远只有 cookie token。
     *    所以它不需要保密，也不需要随 App 重装保留（重装了就是新手机，合理）。
     */
    suspend fun deviceUid(): String {
        val existing = context.dataStore.data.map { it[Keys.DEVICE_UID] ?: "" }.first()
        if (existing.isNotEmpty()) return existing
        val generated = java.util.UUID.randomUUID().toString().replace("-", "").take(16)
        context.dataStore.edit { it[Keys.DEVICE_UID] = generated }
        return generated
    }

    /** 上一次「完整同步」成功的时间（epoch 毫秒）；0 = 从未同步过。 */
    suspend fun lastSyncAt(): Long =
        context.dataStore.data.map { (it[Keys.LAST_SYNC_AT] ?: "").toLongOrNull() ?: 0L }.first()

    suspend fun saveLastSyncAt(epochMillis: Long) {
        context.dataStore.edit { it[Keys.LAST_SYNC_AT] = epochMillis.toString() }
    }

    /**
     * 记住上次连上的**公司名称**。
     *
     * 设置页顶部要显示「连到的这台电脑叫什么」，这个值只能来自服务端 meta。
     * 但断网时 meta 拿不到 —— 不落盘的话标题会退回「简单ERP」，
     * 而用户在设置页最想确认的恰恰是「我现在连的是哪家店」。
     * 所以连上时写一份本地副本，离线时用来兜底（不上报、不参与任何业务）。
     */
    suspend fun shopName(): String =
        context.dataStore.data.map { it[Keys.SHOP_NAME] ?: "" }.first()

    suspend fun saveShopName(name: String) {
        if (name.isBlank()) return
        context.dataStore.edit { it[Keys.SHOP_NAME] = name }
    }

    suspend fun clear() {
        context.dataStore.edit {
            it.remove(Keys.BASE_URL)
            // 断开的语义是「不再连这台电脑」，公司名一起清掉，避免下次连别的店还显示旧名字。
            it.remove(Keys.SHOP_NAME)
            // 上次同步时间一起清：它属于「连的是这台电脑」这件事。
            it.remove(Keys.LAST_SYNC_AT)
            // 「确认过配对」也属于「连的是这台电脑」：断开后再连回来必须重新确认。
            it.remove(Keys.PAIRED_CONFIRMED)
            // ⚠️ `device_uid` **不能**清 —— 它是「这台手机是谁」，与连哪台电脑无关。
            // 清了它，下次连回来服务端会当成新设备，重复配对的问题就回来了。
        }
    }

    companion object {
        /**
         * 归一化委托给 [ServerAddress.normalize] —— 那边是纯函数，能被单元测试覆盖。
         * 这里保留同名入口，调用方不用改。
         */
        fun normalize(raw: String): String = ServerAddress.normalize(raw)
    }
}
