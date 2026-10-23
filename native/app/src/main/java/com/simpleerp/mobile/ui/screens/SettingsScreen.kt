package com.simpleerp.mobile.ui.screens

import com.simpleerp.mobile.BuildConfig
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.data.MetaResponse
import com.simpleerp.mobile.ui.Tab
import com.simpleerp.mobile.ui.components.ErpCard
import com.simpleerp.mobile.ui.components.ErpHairline
import com.simpleerp.mobile.ui.components.LabelValueRow
import com.simpleerp.mobile.ui.components.SectionLabel
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.theme.ThemeMode
import com.simpleerp.mobile.ui.util.formatCacheTime

/**
 * 设置页：**没有任何写端**。
 *
 * 与网页版同一条约束（决策文档 §4 约束 3）：主题切换只是本地偏好，
 * 「重新配对」只是跳转，「解除本机配对」这个按钮**故意不做** ——
 * 那会变成第二个写端。彻底断开设备只能在电脑端撤销。
 */
@Composable
fun SettingsScreen(
    baseUrl: String,
    meta: MetaResponse?,
    /** 连到的那台电脑的**公司名称**（服务端 `config.shop_name`；断网时是本地副本）。 */
    shopName: String,
    themeMode: ThemeMode,
    cacheNewest: Long,
    onThemeMode: (ThemeMode) -> Unit,
    onChangeAddress: () -> Unit,
    onRefresh: () -> Unit,
    onDisconnect: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    // 「断开连接」的二次确认开关。放在这里而不是 SmallAction 内部，
    // 因为弹窗要盖在整个页面之上，不能塞进 LazyColumn 的 item 里。
    var showDisconnectConfirm by remember { mutableStateOf(false) }

    if (showDisconnectConfirm) {
        // 把「会发生什么」说清楚，尤其是「电脑那边不受影响」——
        // 用户最怕的是「我点一下会不会把店里的数据搞坏」。
        AlertDialog(
            onDismissRequest = { showDisconnectConfirm = false },
            title = { Text("断开这台手机？", color = colors.ink, fontSize = 17.sp) },
            text = {
                Text(
                    "断开后要重新扫码输配对码才能再用。电脑上的数据和其他设备都不受影响。",
                    color = colors.muted,
                    fontSize = 13.sp,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    showDisconnectConfirm = false
                    onDisconnect()
                }) {
                    Text("断开", color = colors.danger, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                }
            },
            dismissButton = {
                TextButton(onClick = { showDisconnectConfirm = false }) {
                    Text("取消", color = colors.muted, fontSize = 14.sp)
                }
            },
            containerColor = colors.surface,
        )
    }

    LazyColumn(
        modifier = modifier.fillMaxWidth(),
        contentPadding = PaddingValues(
            start = Dimens.pageGap,
            end = Dimens.pageGap,
            top = 12.dp,
            bottom = 28.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Column(modifier = Modifier.padding(bottom = 4.dp)) {
                // 标题显示**连到的那家店**，不写程序名（用户 2026-10-23 要求）。
                // 「简单ERP」是程序名，摆在标题上等于没回答「我现在连的是哪台电脑」；
                // 服务端没给（旧版本 / 断网且没副本）才退回程序名。
                Text(
                    shopName.ifBlank { meta?.shopName?.ifBlank { null } ?: "简单ERP" },
                    color = colors.ink,
                    fontSize = 21.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(4.dp))
                Text("手机只读端（原生）", color = colors.muted, fontSize = 12.5.sp)
            }
        }

        item {
            ErpCard {
                Text("连接", color = colors.muted, fontSize = 11.5.sp)
                Spacer(Modifier.height(10.dp))
                LabelValueRow("电脑地址", baseUrl.ifEmpty { "未设置" })
                LabelValueRow("电脑上的版本", meta?.appVersion?.ifEmpty { "—" } ?: "—")
                // 必须读 BuildConfig，不能写死：写死的话改了 native/VERSION 也不会变，
                // 用户看到的版本号永远是 0.1.0（真机反馈踩到）。
                LabelValueRow("本机 App 版本", BuildConfig.VERSION_NAME)
                if (meta != null && meta.device.pairedAt.isNotEmpty()) {
                    LabelValueRow("配对时间", meta.device.pairedAt)
                }
                if (meta != null && meta.device.lastSeenAt.isNotEmpty()) {
                    LabelValueRow("最后访问", meta.device.lastSeenAt)
                }
                Spacer(Modifier.height(10.dp))
                // 顺序按用户 2026-10-23 指定：改地址 → 断开连接 → 重新检测。
                // 「改地址」和「断开连接」都是「换/不连这台电脑」，放在最前面；
                // 「重新检测」只是重试一次探活，最容易连点，放最后避免误触。
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    SmallAction("改地址", onChangeAddress)
                    // 「断开连接」必须**二次确认**：它清掉本机配对，
                    // 误触就要重新扫码输码，柜台前很烦。
                    SmallAction("断开连接", { showDisconnectConfirm = true })
                    SmallAction("重新检测", onRefresh)
                }
            }
        }


        item { SectionLabel("外观", modifier = Modifier.padding(top = 6.dp)) }
        item {
            ErpCard(padding = 0.dp) {
                Column {
                    ThemeRow("跟随系统", themeMode == ThemeMode.SYSTEM) {
                        onThemeMode(ThemeMode.SYSTEM)
                    }
                    ErpHairline(Modifier.padding(horizontal = 16.dp))
                    ThemeRow("浅色", themeMode == ThemeMode.LIGHT) { onThemeMode(ThemeMode.LIGHT) }
                    ErpHairline(Modifier.padding(horizontal = 16.dp))
                    ThemeRow("深色", themeMode == ThemeMode.DARK) { onThemeMode(ThemeMode.DARK) }
                }
            }
        }

        item { SectionLabel("离线缓存", modifier = Modifier.padding(top = 6.dp)) }
        item {
            ErpCard {
                Text(
                    if (cacheNewest > 0) {
                        "断网时能看截至 ${formatCacheTime(cacheNewest)} 的数据，页面上会标「离线」。"
                    } else {
                        "还没有缓存。成功联网看过一次之后，断网就能看到上次的数据。"
                    },
                    color = colors.muted,
                    fontSize = 12.5.sp,
                )
            }
        }

        item { SectionLabel("关于", modifier = Modifier.padding(top = 6.dp)) }
        item {
            ErpCard {
                Text(
                    "手机端只能看，不能开单、不能改任何数据。所有查询走只读连接，"
                        + "设备可以在电脑端撤销，撤销后这台手机立刻失去访问。",
                    color = colors.muted,
                    fontSize = 12.5.sp,
                )
                Spacer(Modifier.height(10.dp))
                Text(
                    "主题之外这里没有任何写操作 —— 「解除配对」故意不在手机上做，"
                        + "请在电脑的设置页撤销设备。",
                    color = colors.muted.copy(alpha = 0.85f),
                    fontSize = 11.5.sp,
                )
            }
        }
    }
}

@Composable
private fun ThemeRow(label: String, selected: Boolean, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = colors.ink, fontSize = 14.sp, modifier = Modifier.weight(1f))
        if (selected) {
            Text("✓", color = colors.primary, fontSize = 15.sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun SmallAction(label: String, onClick: () -> Unit) {
    val colors = LocalErpColors.current
    Box(
        modifier = Modifier
            .height(38.dp)
            .clip(RoundedCornerShape(Dimens.radiusSm))
            .background(colors.soft)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = colors.primary, fontSize = 12.5.sp, fontWeight = FontWeight.Medium)
    }
}
