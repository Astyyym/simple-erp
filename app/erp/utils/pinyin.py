"""商品名称 → 拼音首字母（用于商品搜索的拼音匹配）。

背景：`products.pinyin_initials` 字段与 `api_products` 的 `p.pinyin_initials LIKE ?` 早已存在，
但全仓库从未写入过该字段（2026-10-08 复核 #19），拼音搜索实际不可用。
本模块把生成逻辑收敛到一处，供商品新增/编辑/导入与开单自动建档共用。

`pypinyin` 在 requirements.txt 中列出；缺失时退化为空串（不阻断主流程）。
"""
from __future__ import annotations

try:  # pragma: no cover - 导入分支取决于运行环境
    from pypinyin import Style, lazy_pinyin
except ImportError:  # pragma: no cover
    lazy_pinyin = None
    Style = None

MAX_INITIALS_LENGTH = 32


def pinyin_initials(name: str) -> str:
    """返回商品名的拼音首字母（小写、去空白、截断）。

    - 中文 → 拼音首字母（消防泵 → xfb）
    - 非中文（字母/数字）直接保留并小写
    - 任何异常或缺少 pypinyin → 返回空串，绝不让派生字段阻断主流程
    """
    text = (name or "").strip()
    if not text or lazy_pinyin is None:
        return ""
    try:
        parts = lazy_pinyin(text, style=Style.FIRST_LETTER, errors="default")
    except Exception:  # pragma: no cover - 防御性
        return ""
    initials = "".join(parts).replace(" ", "").lower()
    return initials[:MAX_INITIALS_LENGTH]
