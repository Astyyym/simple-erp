"""商品名称 → 拼音首字母（用于商品搜索的拼音匹配）。

背景：`products.pinyin_initials` 字段与 `api_products` 的 `p.pinyin_initials LIKE ?` 早已存在，
但全仓库从未写入过该字段（2026-10-08 复核 #19），拼音搜索实际不可用。
本模块把生成逻辑收敛到一处，供商品新增/编辑/导入与开单自动建档共用。

2026-10-14：只有首字母时「xf」能命中、「xiaofang」「shuiqiang」命中不了（实测），
而门店手输完整拼音是常见动作，故新增 `pinyin_full`（整串小写全拼）与首字母并存；
匹配统一走 `name_match_clause()` / `name_match_params()`，避免新加的搜索点漏掉一列。

2026-10-14 二次实测：门店在开单页输全拼时，中文输入法会在音节之间插入**隔音符号**
（`xiao'fang`，直撇号 U+0027 或弯撇号 U+2019），偶尔还带空格（`xiao fang`）或全角字母
（`ｘｉａｏｆａｎｇ`）。这些串按原样 LIKE 一个都命中不了 —— 用户看到的就是「全拼搜不到」。
所以检索串必须先经 `normalize_name_query()` 归一（NFKC + 去分隔符/空白），
再进 `name_match_params()`；派生列的存法不变（本来就是无分隔符形态）。

`pypinyin` 在 requirements.txt 中列出；缺失时退化为空串（不阻断主流程）。
"""
from __future__ import annotations

import unicodedata

# Deferred pypinyin: imported on first use so app boot does not pay its cost
# (and so a missing package never blocks import). Kept as module-level names so
# callers/tests can still observe or override them.
lazy_pinyin = None
Style = None

# 输入法/键盘会在音节之间插入的分隔符与空白。中文拼音输入法的「隔音符号」就是直/弯撇号，
# 门店按音节打全拼时几乎必然带上；反引号与全角撇号是同类输入的变体。
PINYIN_SEPARATORS = ("'", "’", "‘", "`", "＇", " ", "\u3000", "\t")


def _ensure_pypinyin() -> bool:
    """首次使用时才导入 pypinyin；缺包时仍退化为空串。"""
    global lazy_pinyin, Style
    if lazy_pinyin is not None:
        return True
    try:  # pragma: no cover - 导入分支取决于运行环境
        from pypinyin import Style as _style, lazy_pinyin as _lazy
    except ImportError:  # pragma: no cover
        return False
    lazy_pinyin, Style = _lazy, _style
    return True


MAX_INITIALS_LENGTH = 32
# 全拼比首字母长得多（「消防水枪QZ3.5/7.5」→ 26 字符），上限放宽到 96 覆盖长品名。
MAX_FULL_LENGTH = 96


def pinyin_initials(name: str) -> str:
    """返回商品名的拼音首字母（小写、去空白、截断）。

    - 中文 → 拼音首字母（消防泵 → xfb）
    - 非中文（字母/数字）直接保留并小写
    - 任何异常或缺少 pypinyin → 返回空串，绝不让派生字段阻断主流程
    """
    text = (name or "").strip()
    if not text or not _ensure_pypinyin():
        return ""
    try:
        parts = lazy_pinyin(text, style=Style.FIRST_LETTER, errors="default")
    except Exception:  # pragma: no cover - 防御性
        return ""
    initials = "".join(parts).replace(" ", "").lower()
    return initials[:MAX_INITIALS_LENGTH]


def pinyin_full(name: str) -> str:
    """返回名称的整串小写全拼（消防水枪QZ3.5/7.5 → xiaofangshuiqiangqz3.5/7.5）。

    - 中文 → 完整拼音（不取首字母），非中文（字母/数字/符号）原样保留并小写
    - 与 `pinyin_initials` 同一套容错：异常或缺包 → 空串
    """
    text = (name or "").strip()
    if not text or not _ensure_pypinyin():
        return ""
    try:
        parts = lazy_pinyin(text, errors="default")
    except Exception:  # pragma: no cover - 防御性
        return ""
    full = "".join(parts).replace(" ", "").lower()
    return full[:MAX_FULL_LENGTH]


def pinyin_fields(name: str) -> tuple[str, str]:
    """一次算出 (首字母, 全拼)。写入点统一用它，避免只写一列。"""
    return pinyin_initials(name), pinyin_full(name)


# 名称模糊匹配的三列：名称本身、拼音首字母、拼音全拼。
NAME_MATCH_COLUMNS = ("name", "pinyin_initials", "pinyin_full")


def name_match_clause(prefix: str = "") -> str:
    """三列同参的匹配片段：`(name LIKE ? OR pinyin_initials LIKE ? OR pinyin_full LIKE ?)`。

    `prefix` 是表别名（`"p."` / `"c."`）；参数值三列相同，配 `name_match_params()` 使用。
    """
    return "(" + " OR ".join(f"{prefix}{column} LIKE ?" for column in NAME_MATCH_COLUMNS) + ")"


def normalize_name_query(query: str) -> str:
    """把用户输入的检索串归一成可与派生列比较的形态。

    - NFKC：全角字母/数字/空格 → 半角（`ｘｉａｏｆａｎｇ` → `xiaofang`）
    - 去掉拼音音节分隔符与空白：`xiao'fang` / `xiao’fang` / `xiao fang` → `xiaofang`

    派生列本身就是「无分隔符、无空格、小写」形态，所以只有把检索串归一，
    门店按音节打出来的全拼才能命中。
    """
    text = unicodedata.normalize("NFKC", str(query or ""))
    for separator in PINYIN_SEPARATORS:
        text = text.replace(separator, "")
    return text.strip()


def name_like_pattern(query: str) -> str:
    """归一后的 LIKE 参数（`%…%`），供所有「名称/拼音/快照名」比较点复用。

    只输分隔符/空白（如单个 `'`）时归一结果为空串，直接 LIKE `%%` 会**列出全部记录**；
    这种串退回按原样比较：几乎必然查不到，也不会把整库当成结果。
    """
    raw = str(query or "")
    normalized = normalize_name_query(raw)
    if not normalized and raw.strip():
        normalized = raw.strip()
    return f"%{normalized}%"


def name_match_params(query: str) -> list[str]:
    """`name_match_clause()` 对应的参数列表（顺序与列一致），入参先经归一。"""
    like = name_like_pattern(query)
    return [like] * len(NAME_MATCH_COLUMNS)
