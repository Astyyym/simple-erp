package com.simpleerp.mobile

import android.content.res.Configuration
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.simpleerp.mobile.ui.AppRoot
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        /*
         * 强制 App 内使用简体中文。
         *
         * 为什么（2026-10-23）：material3 的组件文案（日历、选择器、弹窗按钮）取自
         * **系统 Locale**。门店手机本来是中文，没问题；但装机/调试机常是英文系统，
         * 会出现 "Select dates" / "Cancel" 之类，与整个中文界面割裂。
         * App 是给中文门店用的，界面语言不跟随系统更稳。
         *
         * 注：1.4.4 起「筛选时间」的日历已换成自绘中文月历（components/ErpCalendar.kt），
         * 不再依赖 material3 的 DateRangePicker；这条 Locale 钉死仍然要留 ——
         * 底部面板、日期选择等 material3 组件的默认文案依旧取自系统。
         *
         * 只在 App 内生效（覆盖 resources 配置），不改系统设置。
         */
        applyChineseLocale()
        enableEdgeToEdge()
        setContent { AppRoot() }
    }

    private fun applyChineseLocale() {
        val locale = Locale.SIMPLIFIED_CHINESE
        Locale.setDefault(locale)
        val config = Configuration(resources.configuration)
        config.setLocale(locale)
        @Suppress("DEPRECATION")
        resources.updateConfiguration(config, resources.displayMetrics)
    }
}
