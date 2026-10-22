package com.simpleerp.mobile.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.ui.components.ErpCard
import com.simpleerp.mobile.ui.components.PrimaryButton
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors

/**
 * 首次配置：填电脑地址（可选扫码）。
 *
 * 两步是分开的，不能合并：
 * 1. 地址 → 决定连哪台电脑（`base_url` 不编译进包，IP 漂移要能改）
 * 2. 配对码 → 决定这台手机被不被允许读
 *
 * 合并成一个表单会让「地址填错」和「配对码过期」这两种故障显示同一条错误，
 * 而它们的处置方式完全不同。
 */
@Composable
fun SetupScreen(
    onSubmit: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalErpColors.current
    var address by remember { mutableStateOf("") }
    var scanError by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.pageGap, vertical = 32.dp),
    ) {
        Text("连接电脑", color = colors.ink, fontSize = 24.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        Text(
            "手机要和电脑连同一个 WiFi。地址在电脑的设置页能看到。",
            color = colors.muted,
            fontSize = 13.5.sp,
        )

        Spacer(Modifier.height(22.dp))

        // 扫码是主路径：它把「当前地址」直接搬过来，用户不用知道 IP。
        // 手输是备选（电脑设置页的二维码扫不出来时用）。
        ScanQrButton(
            onScanned = { scanned ->
                scanError = null
                address = scanned
                // 扫到就直接连，省掉「再点一次连接」这一步。
                onSubmit(scanned)
            },
            onError = { message -> scanError = message },
        )

        if (scanError != null) {
            Spacer(Modifier.height(10.dp))
            Text(scanError!!, color = colors.danger, fontSize = 12.5.sp)
        }

        Spacer(Modifier.height(14.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.weight(1f).height(1.dp).background(colors.border))
            Text(
                "  或者手输  ",
                color = colors.muted,
                fontSize = 11.5.sp,
            )
            Box(Modifier.weight(1f).height(1.dp).background(colors.border))
        }

        Spacer(Modifier.height(14.dp))
        ErpCard {
            Text("电脑地址", color = colors.muted, fontSize = 11.5.sp)
            Spacer(Modifier.height(10.dp))
            val shape = RoundedCornerShape(Dimens.radiusSm)
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(Dimens.minTouch)
                    .clip(shape)
                    .background(colors.soft)
                    .padding(horizontal = 14.dp),
                contentAlignment = Alignment.CenterStart,
            ) {
                BasicTextField(
                    value = address,
                    onValueChange = { address = it },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    textStyle = TextStyle(color = colors.ink, fontSize = 15.sp),
                    cursorBrush = SolidColor(colors.primary),
                    modifier = Modifier.fillMaxWidth(),
                    decorationBox = { inner ->
                        if (address.isEmpty()) {
                            Text(
                                "192.168.10.5:5001",
                                color = colors.muted.copy(alpha = 0.65f),
                                fontSize = 15.sp,
                            )
                        }
                        inner()
                    },
                )
            }
            Spacer(Modifier.height(8.dp))
            Text(
                "只填 IP 也行，会自动补 http:// 和 5001 端口。",
                color = colors.muted,
                fontSize = 11.5.sp,
            )
        }

        Spacer(Modifier.height(20.dp))
        PrimaryButton(
            text = "连接",
            enabled = address.isNotBlank(),
            onClick = { onSubmit(address.trim()) },
        )

        Spacer(Modifier.height(28.dp))
        ErpCard {
            Text("连不上怎么办", color = colors.ink, fontSize = 13.5.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(10.dp))
            listOf(
                "电脑上的简单ERP要开着，并打开「手机访问」开关",
                "手机和电脑必须连同一个 WiFi（不能用 4G）",
                "第一次连会弹防火墙提示，点「允许」",
                "地址变了就在这台手机里改，不用重装",
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

/**
 * 配对页：输入电脑上显示的 6 位码。
 *
 * 6 位码的安全口径全部在服务端（3 次/分、5 次失败锁 5 分钟、120 秒过期），
 * 客户端只负责把码传上去、把错误原样显示 —— 不自己判断「还剩几次」。
 */
@Composable
fun PairScreen(
    address: String,
    onSubmit: (String) -> Unit,
    modifier: Modifier = Modifier,
    error: String? = null,
    onChangeAddress: (() -> Unit)? = null,
) {
    val colors = LocalErpColors.current
    var code by remember { mutableStateOf("") }
    var localError by remember { mutableStateOf<String?>(null) }
    // 外部 error 是上一次提交的结果，属于「历史」。用户一旦重新提交，
    // 它就该失效 —— 否则会出现「输了新码，界面还挂着上一句『配对码已过期』」，
    // 用户会以为新码也不对（真机实测踩到）。
    var staleErrorCleared by remember { mutableStateOf(false) }
    // 父层给了新 error（新一轮结果回来），说明这次提交有结论了，重新允许显示。
    LaunchedEffect(error) { if (error != null) staleErrorCleared = false }
    val focus = LocalFocusManager.current

    // IME 的 onDone 回调是在 BasicTextField 内部创建的，捕获的是**那一刻**的 code。
    // 用户输完第 6 位后立刻按「完成」，闭包里读到的可能是 5 位的旧值 —— 表现就是
    // 「按了完成但什么都没发生」。用 rememberUpdatedState 让回调永远读最新值。
    val latestCode by rememberUpdatedState(code)
    val submit: () -> Unit = {
        val value = latestCode
        if (value.length == 6) {
            focus.clearFocus()
            localError = null
            staleErrorCleared = true
            onSubmit(value)
        } else {
            localError = "请输入电脑上显示的 6 位数字。"
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            // imePadding：键盘弹出时把内容整体上推，否则「配对」按钮会被键盘盖住，
            // 用户点不到（真机实测：点击落在了键盘区域，请求根本没发出）。
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.pageGap, vertical = 32.dp),
    ) {
        Text("配对这台手机", color = colors.ink, fontSize = 24.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        Text(
            "在电脑的「设置 → 手机访问」里点生成配对码，把 6 位数字输在这里。",
            color = colors.muted,
            fontSize = 13.5.sp,
        )

        Spacer(Modifier.height(22.dp))
        ErpCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("电脑", color = colors.muted, fontSize = 11.5.sp)
                Spacer(Modifier.weight(1f))
                Text(address, color = colors.ink, fontSize = 12.5.sp)
            }
            if (onChangeAddress != null) {
                Spacer(Modifier.height(10.dp))
                // 两个坑，都是真机实测踩出来的：
                // 1. Text 直接放 Column 里会撑满整行，点击热区变成整条卡片宽度；
                // 2. 更要命的：`clickable` 会注册键盘事件处理，输入框的 Enter/Done
                //    键冒泡上来会**激活它** —— 表现就是「按完成想配对，结果地址被清空」。
                //    所以这里用 `pointerInput` 只处理触摸，不参与键盘事件。
                Row(modifier = Modifier.fillMaxWidth()) {
                    Text(
                        "换个地址",
                        color = colors.primary,
                        fontSize = 12.5.sp,
                        modifier = Modifier
                            .pointerInput(Unit) {
                                detectTapGestures(onTap = { onChangeAddress() })
                            }
                            .padding(vertical = 6.dp, horizontal = 2.dp),
                    )
                }
            }
        }

        Spacer(Modifier.height(18.dp))
        // 6 位码画成**六个独立的圆角方格**（用户 2026-10-22 要求）。
        //
        // 实现要点：真正的输入控件只有一个 `BasicTextField`，透明地铺在六个格子
        // 之上。**不要**做六个各自持焦点的输入框 —— 那样要手写「输满自动跳下一格、
        // 退格跳回上一格、粘贴 6 位要分发、删中间一位要补位」一整套逻辑，
        // 而且在各家输入法上行为不一致（尤其国产输入法的联想、自动填充）。
        // 一个隐藏输入框 + 六格纯展示，键盘行为完全不变，代码也少一个数量级。
        val cellShape = RoundedCornerShape(12.dp)
        val focusRequester = remember { FocusRequester() }
        // 进页面自动聚焦：用户是「看到码 → 掏手机 → 输」，少点一下是一下。
        LaunchedEffect(Unit) { focusRequester.requestFocus() }

        Box(modifier = Modifier.fillMaxWidth()) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(9.dp),
            ) {
                repeat(6) { index ->
                    val char = code.getOrNull(index)
                    // 高亮「下一个待输入的格子」：输满 6 位后高亮最后一格。
                    // 没有这个的话，用户不知道光标在哪，只能靠数字自己数。
                    val active = index == code.length.coerceAtMost(5)
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .height(58.dp)
                            .clip(cellShape)
                            .background(colors.surface)
                            .border(
                                width = if (active) 1.5.dp else 1.dp,
                                color = if (active) colors.primary else colors.border,
                                shape = cellShape,
                            ),
                        contentAlignment = Alignment.Center,
                    ) {
                        if (char != null) {
                            Text(
                                char.toString(),
                                color = colors.ink,
                                fontSize = 24.sp,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                    }
                }
            }

            // 透明输入层：铺满六格。点哪儿都能聚焦，键盘行为跟普通输入框一模一样。
            BasicTextField(
                value = code,
                onValueChange = { input ->
                    // 只留数字、最多 6 位：服务端也校验，但这里先挡住明显无效输入
                    code = input.filter { it.isDigit() }.take(6)
                    localError = null
                },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.NumberPassword,
                    imeAction = ImeAction.Done,
                ),
                // 输完按「完成」直接提交配对：这比「收键盘再找按钮」少一步，
                // 而且键盘挡住按钮时这是唯一可靠的提交路径。
                keyboardActions = KeyboardActions(onDone = { submit() }),
                // 文字与光标都透明 —— 真正显示的是下面六个格子。
                textStyle = TextStyle(color = Color.Transparent, fontSize = 24.sp),
                cursorBrush = SolidColor(Color.Transparent),
                modifier = Modifier
                    .matchParentSize()
                    .focusRequester(focusRequester),
            )
        }

        val message = localError ?: error.takeUnless { staleErrorCleared }
        if (!message.isNullOrBlank()) {
            Spacer(Modifier.height(12.dp))
            Text(message, color = colors.danger, fontSize = 12.5.sp)
        }

        Spacer(Modifier.height(20.dp))
        PrimaryButton(
            text = "配对",
            enabled = code.length == 6,
            onClick = submit,
        )

        Spacer(Modifier.height(24.dp))
        ErpCard {
            Text("配对码只有 2 分钟有效期", color = colors.ink, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(8.dp))
            Text(
                "过期了就在电脑上重新生成。连错 5 次会锁 5 分钟，这是防止别人猜码。",
                color = colors.muted,
                fontSize = 12.sp,
            )
        }
    }
}
