package com.simpleerp.mobile.net

/**
 * 地址归一化的**纯逻辑**，不依赖 Android。
 *
 * 为什么从 `ServerConfig` 里拆出来：`ServerConfig` 需要 `Context` + DataStore，
 * 在 JVM 单元测试里加载不了 —— 直接引用它会让 `PairingQr` 的测试整个
 * `ClassNotFoundException`（踩过）。纯函数单独放，测试才能跑。
 *
 * 规则（用户手输的东西五花八门，这里统一成根地址）：
 * - `192.168.10.5:5001` → `http://192.168.10.5:5001`
 * - `192.168.10.5` → `http://192.168.10.5:5001`（补默认端口）
 * - `http://x:5001/m/` → `http://x:5001`（去路径）
 *
 * 默认补 `http://`（局域网没有证书，https 会直接连不上）与 `5001`
 * （固定端口，架构文档约束 4：端口不漂移）。
 */
object ServerAddress {

    const val DEFAULT_PORT = 5001

    fun normalize(raw: String?): String {
        var text = (raw ?: "").trim()
        if (text.isEmpty()) return ""
        if (!text.startsWith("http://", ignoreCase = true) &&
            !text.startsWith("https://", ignoreCase = true)
        ) {
            text = "http://$text"
        }
        // 去掉路径部分（用户可能粘贴了 /m/ 或 /m/api/meta）
        val schemeEnd = text.indexOf("://") + 3
        val slash = text.indexOf('/', schemeEnd)
        if (slash > 0) text = text.substring(0, slash)
        // 补默认端口
        val hostPart = text.substring(schemeEnd)
        if (!hostPart.contains(':')) text = "$text:$DEFAULT_PORT"
        return text.trimEnd('/')
    }
}
