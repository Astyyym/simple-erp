package com.simpleerp.mobile.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.simpleerp.mobile.net.PairingQr
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors

/**
 * 扫码按钮：扫电脑设置页上的二维码，自动填地址。
 *
 * **这是解决「地址不该硬编码」的正确方案**，比任何写死 IP / 主机名的做法都通用：
 *
 * - 二维码里编的是电脑端**当前**地址，IP 变了就重扫一次，用户永远不用知道 IP 是什么；
 * - 换一台电脑、换一个门店、换一个网段，都是同一个动作；
 * - 不依赖路由器能不能配 DHCP 保留（那需要管理员密码，而且型号各异）。
 *
 * 权限处理：CAMERA 在 Manifest 里声明过，但 Android 6+ 要运行时申请。
 * 被拒绝时不弹系统框，而是把「去设置里开权限」的提示交给调用方显示 ——
 * 反复弹系统框只会让用户点「不再询问」。
 */
@Composable
fun ScanQrButton(
    onScanned: (String) -> Unit,
    onError: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    val context = LocalContext.current
    var pendingScan by remember { mutableStateOf(false) }

    val scanLauncher = rememberLauncherForActivityResult(ScanContract()) { result ->
        val contents = result.contents
        if (contents.isNullOrBlank()) {
            // 用户按了返回键：不算错误，什么都不做。
            return@rememberLauncherForActivityResult
        }
        val address = PairingQr.parse(contents)
        if (address == null) {
            onError("这个二维码不是简单ERP 的地址，请扫电脑设置页上那张。")
        } else {
            onScanned(address)
        }
    }

    fun launchScan() {
        val options = ScanOptions().apply {
            setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            setPrompt("对准电脑设置页上的二维码")
            setBeepEnabled(false)
            setOrientationLocked(false)
            // 不锁定竖屏：柜台前横着扫更顺手。
            // 用我们自己的 Activity：默认的 CaptureActivity 没有返回按钮、
            // 也没有相册入口，手势导航的手机进去就出不来。
            setCaptureActivity(ErpCaptureActivity::class.java)
        }
        scanLauncher.launch(options)
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted && pendingScan) {
            pendingScan = false
            launchScan()
        } else if (!granted) {
            pendingScan = false
            onError("没有相机权限，扫不了码。可以去手机「设置 → 应用 → 简单ERP → 权限」里打开，或者直接手输地址。")
        }
    }

    val shape = RoundedCornerShape(Dimens.radiusSm)
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(Dimens.minTouch)
            .clip(shape)
            .background(colors.soft)
            .clickable {
                val granted = ContextCompat.checkSelfPermission(
                    context, Manifest.permission.CAMERA
                ) == PackageManager.PERMISSION_GRANTED
                if (granted) {
                    launchScan()
                } else {
                    pendingScan = true
                    permissionLauncher.launch(Manifest.permission.CAMERA)
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center,
        ) {
            Text("扫二维码", color = colors.primary, fontSize = 15.sp, fontWeight = FontWeight.Medium)
        }
    }
}
