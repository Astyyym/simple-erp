package com.simpleerp.mobile.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 视觉 token 逐项抄自 `app/erp/static/css/mobile.css` 的 `:root` 与
 * `html[data-theme="dark"]`。**不另立一套配色**：网页版和原生版并排看必须像同一个产品，
 * 否则店主会在两个入口之间反复确认「这是不是同一个系统」。
 */

data class ErpColors(
    val bg: Color,
    val surface: Color,
    val soft: Color,
    val ink: Color,
    val muted: Color,
    val border: Color,
    val borderStrong: Color,
    val primary: Color,
    val primaryInk: Color,
    val primarySoft: Color,
    val success: Color,
    val danger: Color,
    val warn: Color,
    val warnBg: Color,
    val warnBorder: Color,
    /** 卡片投影色。**必须有**：网页版靠 box-shadow 把卡片托起来，
     *  原生版原来只有一道 alpha 0.07 的描边，等于没有层级 —— 整屏看着是一张白纸
     *  （用户 2026-10-22：「颜色太单调了，分不清楚主次」）。 */
    val cardShadow: Color,
    /** 欠款金额色。欠款是店主最关心的数，必须比客户名更抓眼；
     *  但不能用 danger（那是「出错了」的语义），所以单独一档暖色。 */
    val debt: Color,
    /** 圆角矩形的硬描边（用户 2026-10-22 要求「全加黑色描边」）。
     *
     *  浅色模式是纯黑；深色模式**必须是白**——深色背景上的纯黑描边等于没有描边，
     *  加了也看不见，那是自欺欺人。所以这里按模式给反色，不是一刀切的黑。 */
    val outline: Color,
)

private val LightColors = ErpColors(
    bg = Color(0xFFFBFBFD),
    surface = Color(0xFFFFFFFF),
    soft = Color(0xFFF4F5F8),
    ink = Color(0xFF101828),
    muted = Color(0xFF6B7280),
    border = Color(0x12101828),
    borderStrong = Color(0x24101828),
    primary = Color(0xFF2563EB),
    primaryInk = Color(0xFFFFFFFF),
    primarySoft = Color(0xFFEEF4FF),
    success = Color(0xFF16794A),
    danger = Color(0xFFC0392B),
    warn = Color(0xFFA15C07),
    warnBg = Color(0xFFFFF8E6),
    warnBorder = Color(0xFFF3D08A),
    cardShadow = Color(0x1A101828),
    debt = Color(0xFFB4530A),
    outline = Color(0xFF000000),
)

private val DarkColors = ErpColors(
    bg = Color(0xFF0B0F16),
    surface = Color(0xFF171F2B),
    soft = Color(0xFF1E2733),
    ink = Color(0xFFF2F5F9),
    muted = Color(0xFFA9B8CB),
    border = Color(0x17FFFFFF),
    borderStrong = Color(0x2BFFFFFF),
    primary = Color(0xFF4F8CFF),
    primaryInk = Color(0xFF08121F),
    primarySoft = Color(0xFF17253C),
    success = Color(0xFF37D399),
    danger = Color(0xFFFF7B72),
    warn = Color(0xFFF5BD48),
    warnBg = Color(0xFF2E2513),
    warnBorder = Color(0xFF5D4A1C),
    cardShadow = Color(0x99000000),
    debt = Color(0xFFF0A64A),
    outline = Color(0xFFFFFFFF),
)

val LocalErpColors = staticCompositionLocalOf { LightColors }

/** 主题偏好：跟随系统 / 强制浅色 / 强制深色（设置页可切，与网页版一致）。 */
enum class ThemeMode { SYSTEM, LIGHT, DARK }

private val ErpTypography = Typography(
    // 柜台场景：数字要一眼读准，所以数字字号大、行高紧，不用展示型字体。
    headlineMedium = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.SemiBold, lineHeight = 28.sp),
    headlineSmall = TextStyle(fontSize = 19.sp, fontWeight = FontWeight.SemiBold, lineHeight = 25.sp),
    titleMedium = TextStyle(fontSize = 16.sp, fontWeight = FontWeight.Medium, lineHeight = 22.sp),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 21.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 12.5.sp, lineHeight = 17.sp),
    labelSmall = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.Medium, lineHeight = 15.sp),
)

/** 页面级间距常量（对应 CSS 的 `--gap-page: 18px` / `--pad-card: 18px`）。 */
object Dimens {
    val pageGap = 18.dp
    val cardPad = 18.dp
    val radius = 14.dp
    val radiusSm = 11.dp
    val rowPad = 15.dp
    /** 触控目标下限：架构文档要求 ≥ 44px。 */
    val minTouch = 48.dp
}

@Composable
fun SimpleErpTheme(
    mode: ThemeMode = ThemeMode.SYSTEM,
    content: @Composable () -> Unit,
) {
    val dark = when (mode) {
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
        ThemeMode.LIGHT -> false
        ThemeMode.DARK -> true
    }
    val colors = if (dark) DarkColors else LightColors
    val scheme = if (dark) {
        darkColorScheme(
            primary = colors.primary,
            onPrimary = colors.primaryInk,
            background = colors.bg,
            onBackground = colors.ink,
            surface = colors.surface,
            onSurface = colors.ink,
            surfaceVariant = colors.soft,
            onSurfaceVariant = colors.muted,
            error = colors.danger,
        )
    } else {
        lightColorScheme(
            primary = colors.primary,
            onPrimary = colors.primaryInk,
            background = colors.bg,
            onBackground = colors.ink,
            surface = colors.surface,
            onSurface = colors.ink,
            surfaceVariant = colors.soft,
            onSurfaceVariant = colors.muted,
            error = colors.danger,
        )
    }
    /*
     * 状态栏图标颜色必须跟着**App 内**的明暗走，不能跟着系统走。
     *
     * 原来的 bug（用户 2026-10-21 报）：`enableEdgeToEdge()` 不带参数时，
     * 状态栏图标是亮是暗由**系统**深色模式决定。用户把 App 设成「浅色」、
     * 但手机系统是深色 → 浅色页面 + 白色状态栏图标 = 时间/电量看不清。
     *
     * 这里显式设 `isAppearanceLightStatusBars = !dark`：
     * - 浅色主题 → true（图标转黑）
     * - 深色主题 → false（图标转白）
     * 并同时把状态栏设成透明，让 App 背景自己透上去，颜色才连续。
     */
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as? android.app.Activity)?.window ?: return@SideEffect
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = !dark
                isAppearanceLightNavigationBars = !dark
            }
        }
    }

    androidx.compose.runtime.CompositionLocalProvider(LocalErpColors provides colors) {
        MaterialTheme(colorScheme = scheme, typography = ErpTypography, content = content)
    }
}
