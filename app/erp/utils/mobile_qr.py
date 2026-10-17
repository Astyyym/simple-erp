"""手机访问二维码：纯本地生成 PNG，不联网、不依赖任何 CDN。

用 `qrcode`（纯 Python）+ Pillow 现成的 PNG 输出，直接在设置页内联成 data URI。
缺包时返回空串——设置页仍然可用（地址就在旁边，手输也能配对）。
"""
from __future__ import annotations

import base64
import io


def qr_png_data_uri(text: str) -> str:
    payload = str(text or "").strip()
    if not payload:
        return ""
    try:
        import qrcode
    except ImportError:  # pragma: no cover - 依赖缺失时降级，不阻断设置页
        return ""
    try:
        image = qrcode.make(payload)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    except Exception:  # noqa: BLE001 - 二维码只是辅助，失败不该 500
        return ""
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
