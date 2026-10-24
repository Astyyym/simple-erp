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
    }

    /** 返回形如 `http://192.168.10.5:5001` 的根地址；未配置时为空串。 */
    suspend fun baseUrl(): String =
        context.dataStore.data.map { it[Keys.BASE_URL] ?: "" }.first()

    suspend fun saveBaseUrl(raw: String) {
        context.dataStore.edit { it[Keys.BASE_URL] = normalize(raw) }
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
