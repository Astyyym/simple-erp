# 名称检索与拼音匹配（客户 / 商品）

**适用范围**：所有「按名称找客户/商品」的入口 —— 列表页搜索框、联想建议接口、开单页客户与商品输入框、单据筛选（客户）、原单候选、货款汇总的客户解析。

## 规则

1. **不要手写 `name LIKE ?`**。名称匹配统一走 `app/erp/utils/pinyin.py`：
   - `name_match_clause(prefix="")` → `(name LIKE ? OR pinyin_initials LIKE ? OR pinyin_full LIKE ?)`
   - `name_match_params(query)` → 与上面三列顺序一致、**已归一**的参数列表
   - `name_like_pattern(query)` → 单列场景（电话/地址、单据上的客户名快照）用的归一 LIKE 参数

   新增搜索点时漏掉 `pinyin_full` 会让「全拼搜不到」复发；`tests/test_pinyin_full_match.py` 有一条全仓扫描用例守住这件事。

2. **检索串必须先归一**（`normalize_name_query()`：NFKC + 去拼音分隔符与空白）。
   门店用中文输入法按音节打全拼时，输入法会在音节之间插**隔音符号**：`xiao'fang`（直撇号 U+0027 或弯撇号 U+2019），偶尔带空格（`xiao fang`）或全角字母（`ｘｉａｏｆａｎｇ`）。
   派生列存的是**无分隔符**形态（`xiaofangshuidai…`），所以 `%xiao'fang%` 一个都命中不了 —— 用户看到的就是「全拼搜不到」，而首字母与汉字恰好正常。
   这一条曾经被误判成「没做全拼」，实际是没归一。

3. **只输分隔符/空白（如单个 `'`）时不能归一成空串**：`LIKE '%%'` 会把整库列出来。`name_like_pattern()` 在这种情况下退回按原串比较（几乎必然查不到）。

4. **派生列的写入点必须两列一起写**：`pinyin_fields(name)` 一次返回 `(initials, full)`；只写一列会让那一列永远搜不到。老库靠 `init_db()` 里的 `backfill_*_pinyin_*`（只补空值、幂等、**不升 schema 版本**）回填。

## 已知边界（不是缺陷）

- `pypinyin` 的 ü 写作 `v`：`过滤器` → `guolvqi`。用户打 `guoluqi` 搜不到，属已知边界，未做 ü↔u 容忍。
- 多音字按 `pypinyin` 默认读音，不做纠正。
- `pinyin_full` 截断到 96 字符；`pinyin_initials` 截断到 32 字符。
- 缺 `pypinyin` 时两列退化为空串（不阻断启动），此时只剩「名称」一列匹配。

## 验证方式

`tests/test_pinyin_full_match.py` 按**真实入口**逐项断言（不是只看 SQL 文本）：商品/客户列表页、建议接口、开单联想、拿货对象、原单候选、单据筛选、货款汇总解析，另含老库回填、幂等、分隔符归一与「只输分隔符」边界。
