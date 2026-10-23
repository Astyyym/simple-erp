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
    }

    /** 返回形如 `http://192.168.10.5:5001` 的根地址；未配置时为空串。 */
    suspend fun baseUrl(): String =
        context.dataStore.data.map { it[Keys.BASE_URL] ?: "" }.first()

    suspend fun saveBaseUrl(raw: String) {
        context.dataStore.edit { it[Keys.BASE_URL] = normalize(raw) }
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
