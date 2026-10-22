package com.simpleerp.mobile.net

/**
 * 扫码结果 → 服务器地址。
 *
 * 为什么要单独一个解析器：二维码里编的是**电脑端当前**的完整地址
 * （`http://192.168.10.5:5001/m/`），而 IP 会漂移。扫码的意义正是
 * 「不用让用户知道 IP 是什么」，所以这里必须能容忍各种形态：
 *
 * | 扫到的内容 | 解析结果 |
 * |---|---|
 * | `http://192.168.10.5:5001/m/` | `http://192.168.10.5:5001` |
 * | `http://192.168.10.5:5001` | 同上 |
 * | `192.168.10.5:5001` | `http://192.168.10.5:5001` |
 * | `192.168.10.5` | `http://192.168.10.5:5001`（补默认端口） |
 *
 * **不接受**非 http(s) 的内容（比如 `javascript:`、`file:`）：
 * 二维码是可以被任何人贴一张假的，解析器只认网络地址，其余一律当无效。
 */
object PairingQr {

    /** 解析成功返回规范化的根地址，失败返回 null。 */
    fun parse(raw: String?): String? {
        val text = (raw ?: "").trim()
        if (text.isEmpty()) return null

        // 显式拒绝非网络协议：防止扫到恶意二维码把 baseUrl 改成奇怪的东西。
        val lower = text.lowercase()
        if (lower.startsWith("javascript:") ||
            lower.startsWith("file:") ||
            lower.startsWith("content:") ||
            lower.startsWith("intent:")
        ) {
            return null
        }

        // 复用纯函数的归一化：补协议、去路径、补端口都已处理。
        // 刻意**不用** ServerConfig.normalize —— 那个类依赖 Context/DataStore，
        // 在 JVM 测试里会 ClassNotFoundException（踩过）。
        val normalized = ServerAddress.normalize(text)
        if (normalized.isEmpty()) return null

        // 归一化后必须真的像个 http(s) 地址，否则视为无效。
        if (!normalized.startsWith("http://") && !normalized.startsWith("https://")) {
            return null
        }
        // 主机必须是**合法的 IP 或主机名**。
        //
        // 这一步是真机实测补的：只判断「非空」的话，扫到一段普通中文
        // （比如别人家二维码里的说明文字）会被归一化成
        // `http://这是一段普通文字:5001` —— 一个永远连不上的地址，
        // 而用户看到的错误会是「连不上电脑」，排查方向完全错。
        val afterScheme = normalized.substringAfter("://")
        val host = afterScheme.substringBefore(":")
        if (!isValidHost(host)) return null
        return normalized
    }

    /** 主机名/域名/IPv4 的宽松校验：只允许字母、数字、点、短横线。 */
    private fun isValidHost(host: String): Boolean {
        if (host.isBlank() || host.length > 253) return false
        // 局域网场景只有 IPv4 与普通主机名，不处理 IPv6 字面量（带方括号）。
        val allowed = host.all { ch ->
            ch.isLetterOrDigit() && ch.code < 128 || ch == '.' || ch == '-'
        }
        if (!allowed) return false
        // 不能全是点，也不能以点或短横线开头/结尾
        if (host.trim('.').isEmpty()) return false
        if (host.startsWith('.') || host.endsWith('.') ||
            host.startsWith('-') || host.endsWith('-')
        ) return false
        // 每段不能为空（挡住 `1..2` 这种）
        return host.split('.').none { it.isEmpty() }
    }
}
