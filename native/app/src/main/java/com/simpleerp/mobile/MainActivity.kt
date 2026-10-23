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
         * 为什么（2026-10-23）：material3 的 DateRangePicker（「更多筛选」里选日区间）
         * 用的日历文案来自系统 Locale。门店手机本来是中文，没问题；但装机/调试机
         * 常是英文系统，日历会显示 "Select dates" / "Start date - End date"，
         * 与整个中文界面割裂。App 是给中文门店用的，界面语言不跟随系统更稳。
         *
         * 只在 App 内生效（attachBaseContext 覆盖配置），不改系统设置。
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
