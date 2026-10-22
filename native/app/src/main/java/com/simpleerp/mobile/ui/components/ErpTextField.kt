package com.simpleerp.mobile.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.simpleerp.mobile.ui.theme.Dimens
import com.simpleerp.mobile.ui.theme.LocalErpColors

/**
 * 通用输入框。
 *
 * 两个真机实测出来的细节（不是可选项）：
 *
 * 1. **`imeAction = Done` + `keyboardActions` 收起键盘**。否则用 `BasicTextField`
 *    时键盘会一直挡在下面，把「连接 / 配对」按钮盖住，用户点不到。
 * 2. **焦点管理器在提交时主动清焦**。配对页的按钮就在键盘上方，光靠 IME 的 Done
 *    在某些输入法上不生效（实测模拟器上键盘不消失）。
 */
@Composable
fun ErpTextField(
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    keyboardType: KeyboardType = KeyboardType.Text,
    imeAction: ImeAction = ImeAction.Done,
    textSize: Int = 15,
    showClear: Boolean = true,
) {
    val colors = LocalErpColors.current
    val focus = LocalFocusManager.current
    val shape = RoundedCornerShape(Dimens.radiusSm)
    Row(
        modifier = modifier
            .fillMaxWidth()
            .height(Dimens.minTouch)
            .clip(shape)
            .background(colors.surface)
            .padding(horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        BasicTextField(
            value = value,
            onValueChange = onValueChange,
            singleLine = true,
            textStyle = TextStyle(color = colors.ink, fontSize = textSize.sp),
            cursorBrush = SolidColor(colors.primary),
            keyboardOptions = KeyboardOptions(
                keyboardType = keyboardType,
                imeAction = imeAction,
            ),
            keyboardActions = KeyboardActions(onDone = { focus.clearFocus() }),
            modifier = Modifier.weight(1f),
            decorationBox = { inner ->
                if (value.isEmpty()) {
                    Text(placeholder, color = colors.muted, fontSize = textSize.sp)
                }
                inner()
            },
        )
        if (showClear && value.isNotEmpty()) {
            Box(
                modifier = Modifier
                    .clickable { onValueChange("") }
                    .padding(start = 10.dp, top = 4.dp, bottom = 4.dp),
            ) {
                Text("✕", color = colors.muted, fontSize = 14.sp)
            }
        }
    }
}
