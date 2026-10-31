package com.simpleerp.mobile.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.ui.components.ErpCard
import com.simpleerp.mobile.ui.components.ErpHairline
import com.simpleerp.mobile.ui.components.LabelValueRow
import com.simpleerp.mobile.ui.components.PrimaryButton
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors
import com.simpleerp.mobile.ui.util.formatCacheTime

/**
 * 「连不上电脑」页（2026-10-31）。
 *
 * 为什么需要它：原来「探测不到电脑」被直接当成「不需要配对」，App 静默进主界面显示旧缓存。
 * 门店真实事故里，用户看到屏幕上有 10 月 24 日的数据，以为是「配好了」，
 * 而电脑端一个包都没收到 —— 两边都看不出问题在哪。
 *
 * 这一页只回答三个问题：**连的是哪台电脑 / 为什么没连上 / 现在能做什么**。
 * 「先看离线缓存」是**次级**入口（灰字、放下面）：离线看数据是明确的产品能力，
 * 但要让人知道自己看的是旧数据，而不是把它当成「连上了」。
 */
@Composable
fun ConnectionLostScreen(
    address: String,
    detail: String,
    cacheAt: Long,
    onRetry: () -> Unit,
    onChangeAddress: () -> Unit,
    onShowCache: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    val hasCache = cacheAt > 0

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.pageGap, vertical = 32.dp),
    ) {
        Text("连不上电脑", color = colors.ink, fontSize = 24.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        Text(
            "手机没能连上这台电脑。先把网络打通，再回来重试。",
            color = colors.muted,
            fontSize = 13.5.sp,
        )

        Spacer(Modifier.height(22.dp))
        ErpCard {
            LabelValueRow("电脑地址", address.ifBlank { "还没设置" })
            if (hasCache) {
                Spacer(Modifier.height(8.dp))
                ErpHairline()
                Spacer(Modifier.height(8.dp))
                LabelValueRow("缓存时间", formatCacheTime(cacheAt), valueColor = colors.muted)
            }
            if (detail.isNotBlank()) {
                Spacer(Modifier.height(10.dp))
                Text(detail, color = colors.danger, fontSize = 12.sp)
            }
        }

        Spacer(Modifier.height(20.dp))
        PrimaryButton(text = "重试", onClick = onRetry)

        Spacer(Modifier.height(6.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "改地址",
                color = colors.primary,
                fontSize = 13.sp,
                modifier = Modifier
                    .clickable(onClick = onChangeAddress)
                    .padding(vertical = 12.dp, horizontal = 2.dp),
            )
            Spacer(Modifier.weight(1f))
            if (hasCache) {
                Text(
                    "先看离线缓存",
                    color = colors.muted,
                    fontSize = 13.sp,
                    modifier = Modifier
                        .clickable(onClick = onShowCache)
                        .padding(vertical = 12.dp, horizontal = 2.dp),
                )
            }
        }

        Spacer(Modifier.height(18.dp))
        ErpCard {
            Text("按这个顺序查", color = colors.ink, fontSize = 13.5.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(10.dp))
            listOf(
                "手机和电脑连同一个 WiFi（不能用 4G/5G 流量）",
                "电脑上的简单ERP开着，设置页「手机访问」是「已打开」",
                "电脑上第一次打开手机访问时，防火墙弹窗要点「允许」",
                "电脑的 IP 变了就点「改地址」重新扫码，别用手输的旧地址",
            ).forEach { line ->
                Row(modifier = Modifier.padding(vertical = 4.dp)) {
                    Text("·", color = colors.muted, fontSize = 12.5.sp)
                    Spacer(Modifier.width(8.dp))
                    Text(line, color = colors.muted, fontSize = 12.5.sp)
                }
            }
        }
    }
}
