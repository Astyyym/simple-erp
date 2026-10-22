package com.simpleerp.mobile.ui.screens

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.drawable.GradientDrawable
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.doOnLayout
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.client.android.Intents
import com.google.zxing.common.HybridBinarizer
import com.journeyapps.barcodescanner.CaptureManager
import com.journeyapps.barcodescanner.DecoratedBarcodeView
import com.journeyapps.barcodescanner.camera.CameraSettings

/**
 * 扫码界面。
 *
 * 为什么不用库自带的 `CaptureActivity`：它是**全屏相机，上面一个控件都没有** ——
 * 没有返回按钮（手势导航的手机进去就出不来），也没有「从相册选」。
 *
 * 为什么不用 `CaptureActivity` 当基类再叠加控件：那个基类继承的是
 * `android.app.Activity`，拿不到 `registerForActivityResult`，相册只能走
 * 已废弃的 `startActivityForResult` + `onActivityResult`。
 *
 * 所以这里**自己组装**：`ComponentActivity` + 库的 `DecoratedBarcodeView`（相机预览）
 * + `CaptureManager`（生命周期与解码回调）。这样既有现代 Activity Result API，
 * 又不用重写相机那一层。
 */
class ErpCaptureActivity : ComponentActivity() {

    private lateinit var barcodeView: DecoratedBarcodeView
    private lateinit var hint: TextView
    private lateinit var captureManager: CaptureManager

    /**
     * 从相册选图。
     *
     * 用 `GetContent` 而不是 `PickVisualMedia`：后者依赖 Photo Picker
     * （Android 13+ 系统组件），老机型上会退化；`GetContent` 走系统文档选择器，
     * 兼容性更宽，且不需要额外权限。
     */
    private val pickImage = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        // 用户取消选择：什么都不做，继续扫码。静默是对的 —— 他可能只是改主意了。
        if (uri == null) return@registerForActivityResult
        val text = decodeFromUri(uri)
        if (text.isNullOrBlank()) {
            // 必须给提示：否则用户会以为按钮点坏了，反复点同一张图。
            Toast.makeText(this, "这张图片里没找到二维码，换一张试试", Toast.LENGTH_SHORT).show()
        } else {
            returnResult(text)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = FrameLayout(this)

        barcodeView = DecoratedBarcodeView(this).apply {
            // 从 Intent 读条码格式与提示语，与 ScanOptions 的设置保持一致。
            initializeFromIntent(intent)
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
            // ⚠️ **必须打开连续对焦**，这是「对着电脑扫不出来、从相册选就能扫出来」
            // 的真正根因（用户 2026-10-22 真机反馈）。
            //
            // zxing 的 CameraSettings 默认是 autoFocusEnabled=true 但
            // continuousFocusEnabled=false —— 意思是**只对焦一次，之后就不管了**。
            // 手机对着电脑屏幕时，第一次对焦往往落在屏幕外的背景上（键盘、桌面），
            // 之后画面再清楚也不会重新对焦，于是永远解不出码。
            // 相册里的图是清晰的原图，所以那条路一直好使 —— 这也解释了
            // 为什么「相册能扫、相机不能扫」。
            //
            // 关掉 autoFocus 也是对的：它是一次性对焦，和连续对焦同时开会互相打架。
            cameraSettings = CameraSettings().apply {
                isContinuousFocusEnabled = true
                isAutoFocusEnabled = false
                // 条形码场景模式：部分机型在「文字/风景」场景下会自动拉高锐度、
                // 降噪，反而把二维码的方块边缘磨掉。
                isBarcodeSceneModeEnabled = true
                // 测光：屏幕是自发光的，不测光时相机可能把屏幕曝成一片白，
                // 二维码的黑白对比度被吃掉。
                isMeteringEnabled = true
            }
        }
        root.addView(barcodeView)
        root.addView(buildOverlayControls())
        setContentView(root)

        // CaptureManager 负责相机生命周期与解码回调，省掉自己写 CameraX。
        captureManager = CaptureManager(this, barcodeView)
        captureManager.initializeFromIntent(intent, savedInstanceState)
    }

    /**
     * 在相机预览之上叠的控件。
     *
     * 两个按钮都做到 48dp 以上：柜台前单手操作，触摸目标太小等于点不到。
     */
    private fun buildOverlayControls(): ViewGroup {
        val overlay = FrameLayout(this).apply {
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
        }

        // 关闭按钮：**圆形**（用户 2026-10-22 要求统一圆角矩形）。
        // 原来是个 48dp 的方形深灰块，压在相机画面上像一块补丁。
        // 现在用圆形半透明底 + 白色描边，是相机类 App 的通行做法，
        // 既不挡画面又一眼能认。
        val close = TextView(this).apply {
            text = "✕"
            textSize = 19f
            setTextColor(0xFFFFFFFF.toInt())
            gravity = Gravity.CENTER
            contentDescription = "退出扫码"
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(0x8C000000.toInt())
                setStroke(dp(1), 0x4DFFFFFF)
            }
            layoutParams = FrameLayout.LayoutParams(dp(44), dp(44)).apply {
                gravity = Gravity.TOP or Gravity.START
            }
            setOnClickListener { cancelScan() }
        }

        // 相册按钮：**圆角药丸**，跟 App 内其他按钮同一套圆角语言。
        // 原来是个直角矩形色块，在一屏相机画面上像贴上去的标签。
        val gallery = TextView(this).apply {
            text = "从相册选一张"
            textSize = 14.5f
            setTextColor(0xFFFFFFFF.toInt())
            gravity = Gravity.CENTER
            setPadding(dp(24), dp(13), dp(24), dp(13))
            contentDescription = "从相册选择二维码图片"
            background = GradientDrawable().apply {
                cornerRadius = dp(24).toFloat()
                setColor(0x99000000.toInt())
                setStroke(dp(1), 0x40FFFFFF)
            }
            elevation = dp(2).toFloat()
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply {
                gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            }
            setOnClickListener { pickImage.launch("image/*") }
        }

        // 提示文字：**跟着扫描框走**，贴在框的正下方居中（用户 2026-10-22 要求）。
        //
        // 为什么自己画一条而不是用 zxing 的 `zxing_status_view`：那个 TextView 在
        // 库里是 `layout_gravity="bottom|center_horizontal"` 硬编码的，永远贴屏幕
        // 最底边 —— 在全面屏手机上离扫描框有一大段距离，用户根本不会把两者联系起来。
        //
        // 位置在 `onGlobalLayout` 里算：扫描框（framingRect）要等相机起来、
        // 预览尺寸确定之后才有值，onCreate 那一刻还是 null。
        hint = TextView(this).apply {
            text = "对准电脑设置页上的二维码"
            textSize = 13.5f
            setTextColor(0xF2FFFFFF.toInt())
            gravity = Gravity.CENTER
            setPadding(dp(16), dp(9), dp(16), dp(9))
            background = GradientDrawable().apply {
                cornerRadius = dp(20).toFloat()
                setColor(0x99000000.toInt())
            }
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply {
                gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            }
        }

        overlay.addView(close)
        overlay.addView(gallery)
        overlay.addView(hint)

        // 安全区：不加的话 `✕` 会被状态栏压住、底部按钮会被导航条压住。
        ViewCompat.setOnApplyWindowInsetsListener(overlay) { _, insets ->
            val top = insets.getInsets(WindowInsetsCompat.Type.statusBars()).top
            val bottom = insets.getInsets(WindowInsetsCompat.Type.navigationBars()).bottom
            (close.layoutParams as FrameLayout.LayoutParams).apply {
                topMargin = dp(10) + top
                leftMargin = dp(14)
            }
            // 相册按钮**上移**（用户 2026-10-22：「下面这个有点靠下了」）。
            // 原来是 `28dp + 导航条高度`，在全面屏上会贴着屏幕最底边，
            // 拇指要够到很下面。改成固定 96dp + 导航条：既离开底边一段呼吸区，
            // 又落在拇指自然够到的位置（相机预览本身没有内容会挡）。
            (gallery.layoutParams as FrameLayout.LayoutParams).apply {
                bottomMargin = dp(96) + bottom
            }
            close.requestLayout()
            gallery.requestLayout()
            insets
        }

        // 提示文字贴到扫描框正下方。
        //
        // 用 `doOnLayout` + `onGlobalLayout` 两级：
        // - 第一级等 overlay 自己有尺寸；
        // - 第二级等相机起来后 framingRect 才有值（相机是异步初始化的，
        //   扫描框尺寸要等预览分辨率定下来才确定）。
        // 没有第二级的话，提示会停在顶部（topMargin 还是 0）。
        overlay.doOnLayout { placeHintBelowFrame() }
        overlay.viewTreeObserver.addOnGlobalLayoutListener { placeHintBelowFrame() }
        return overlay
    }

    /**
     * 把提示文字摆到扫描框正下方。
     *
     * `getFramingRect()` 返回的**就是 View 坐标系**（ViewfinderView 直接拿它去
     * `canvas.drawRect`，而 canvas 用的是 View 自己的坐标），所以 `rect.bottom`
     * 可以直接当 `topMargin` 用，**不需要**再按预览分辨率换算 —— 我第一版多乘了
     * 一个 scale，结果提示被推到屏幕中间、和「从相册选一张」叠在一起了。
     *
     * 两个兜底：
     * 1. 拿不到框（相机还没起来）时直接返回，下一轮布局会再进来；
     * 2. 框底部已经很低（小屏 / 预览被裁得厉害）时，把提示夹在相册按钮上方，
     *    否则两者会重叠。
     */
    private fun placeHintBelowFrame() {
        val frame = barcodeView.barcodeView.framingRect ?: return
        val parentHeight = (hint.parent as? View)?.height ?: return
        if (parentHeight <= 0 || hint.height <= 0) return

        var top = frame.bottom + dp(18)

        // 兜底：给相册按钮留位置。按钮底边距 = 96dp + 导航条，自身高约 48dp，
        // 再留 16dp 间隙；算出来比这个还低就往上夹。
        val galleryReserve = dp(96 + 48 + 16)
        val maxTop = parentHeight - galleryReserve - hint.height
        if (maxTop > 0 && top > maxTop) top = maxTop

        (hint.layoutParams as FrameLayout.LayoutParams).apply {
            topMargin = top
        }
        hint.requestLayout()
    }

    /** 取消 = 不返回内容。上层把 null 当成「用户放弃了」，不弹错误。 */
    private fun cancelScan() {
        setResult(RESULT_CANCELED)
        finish()
    }

    /** 把识别结果交回给 `ScanContract`。 */
    private fun returnResult(text: String) {
        setResult(RESULT_OK, Intent().putExtra(Intents.Scan.RESULT, text))
        finish()
    }

    // ---------------------------------------------------------------------
    // 图片识别
    // ---------------------------------------------------------------------

    /**
     * 图片 → 二维码文本。返回 null 表示这张图里没有可识别的码。
     *
     * 用 zxing 核心库直接解，不走 `IntentIntegrator`：我们只需要「一张图进、一段文本出」，
     * 引一整套 UI 流程反而更绕。
     */
    private fun decodeFromUri(uri: Uri): String? {
        val bitmap = loadScaledBitmap(uri) ?: return null
        val width = bitmap.width
        val height = bitmap.height
        if (width <= 0 || height <= 0) return null
        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)

        // 多试几轮，从「最省事」到「最费劲」：
        //
        // 1. 只认二维码 + 普通二值化 —— 覆盖绝大多数情况，也最快；
        // 2. 只认二维码 + TRY_HARDER —— 照片里有反光、轻微变形、码偏小的时候，
        //    这一步能把「解不出来」变成「解得出来」，代价是慢几十毫秒；
        // 3. 全格式 + TRY_HARDER —— 万一用户拍的是别人家 App 生成的二维码，
        //    或者是条码，也给他一次机会（解析器后面还会校验主机名）。
        //
        // 为什么要显式指定 QR_CODE：`MultiFormatReader()` 不带参数时是
        // 「所有格式都试」，在没有 TRY_HARDER 的情况下反而容易在噪声上失败；
        // 明确告诉它「我只要二维码」能显著提高成功率。
        val attempts = listOf(
            mapOf(DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE)),
            mapOf(
                DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE),
                DecodeHintType.TRY_HARDER to true,
            ),
            mapOf(DecodeHintType.TRY_HARDER to true),
        )

        attempts.forEach { hints ->
            val text = runCatching {
                val binary = BinaryBitmap(
                    HybridBinarizer(RGBLuminanceSource(width, height, pixels))
                )
                val reader = MultiFormatReader()
                try {
                    reader.setHints(hints)
                    reader.decodeWithState(binary).text
                } finally {
                    reader.reset()
                }
            }.getOrNull()
            if (!text.isNullOrBlank()) return text
        }

        // 最后一招：二维码太小（截图缩放过）时，把图放大再试一次。
        // 相册里从网页截的图，码可能只有 100px 宽，zxing 在这么小的图上
        // 定位图案容易失败。放大 2 倍基本能救回来。
        return runCatching {
            val scaled = Bitmap.createScaledBitmap(bitmap, width * 2, height * 2, true)
            val w2 = scaled.width
            val h2 = scaled.height
            val px = IntArray(w2 * h2)
            scaled.getPixels(px, 0, w2, 0, 0, w2, h2)
            val binary = BinaryBitmap(HybridBinarizer(RGBLuminanceSource(w2, h2, px)))
            val reader = MultiFormatReader()
            try {
                reader.setHints(
                    mapOf(
                        DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE),
                        DecodeHintType.TRY_HARDER to true,
                    )
                )
                reader.decodeWithState(binary).text
            } finally {
                reader.reset()
            }
        }.getOrNull()
    }

    /**
     * 读图并按需降采样。
     *
     * **必须降采样**：相册原图动辄 4000×3000，`getPixels` 要分配 `w*h*4` 字节
     * （约 48MB），在中低端机上会直接 OOM 或卡住几百毫秒。
     * 二维码在 1600px 以内完全够识别，所以按 `inSampleSize` 逐级减半。
     *
     * 分两趟读：第一趟只取尺寸（`inJustDecodeBounds`，不分配像素内存），
     * 算出采样率；第二趟才真正解码。
     */
    private fun loadScaledBitmap(uri: Uri): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        try {
            contentResolver.openInputStream(uri)?.use {
                BitmapFactory.decodeStream(it, null, bounds)
            }
        } catch (e: Exception) {
            return null
        }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

        var sample = 1
        while (bounds.outWidth / sample > MAX_DECODE_SIDE ||
            bounds.outHeight / sample > MAX_DECODE_SIDE
        ) {
            sample *= 2
        }
        val options = BitmapFactory.Options().apply { inSampleSize = sample }
        return try {
            contentResolver.openInputStream(uri)?.use { stream ->
                BitmapFactory.decodeStream(stream, null, options)
            }
        } catch (e: Exception) {
            null
        }
    }

    // ---------------------------------------------------------------------
    // 生命周期：交给 CaptureManager，否则相机不释放、回来一片黑
    // ---------------------------------------------------------------------

    override fun onResume() {
        super.onResume()
        captureManager.onResume()
    }

    override fun onPause() {
        super.onPause()
        captureManager.onPause()
    }

    override fun onDestroy() {
        super.onDestroy()
        captureManager.onDestroy()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        captureManager.onSaveInstanceState(outState)
    }

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt()

    private companion object {
        /** 解码时的最长边上限。二维码在这个尺寸内完全够识别。 */
        const val MAX_DECODE_SIDE = 1600
    }
}
