# 商品身份：单一名称（v14）与 spec 合并迁移

**当前规则（2026-10-13 起，schema 14）**：商品身份就是**商品名称本身**。原「名称 + 型号」两段身份已合并，型号并入名称、**直接拼接、中间不加任何字符**（`闸阀` + `DN50` → `闸阀DN50`）。规格信息不再单独保留。

## 落到代码上的事实

- `products.spec`、`order_items.spec`、`purchase_order_items.spec`、`purchase_return_items.spec` **四列已删除**；任何手写 SQL、夹具、导出/导入模板都不得再出现 `spec`。
- 唯一索引：`idx_products_business_identity ON products(TRIM(name))`（含软删除行）；重复建档报「商品名称已存在」。
- 服务签名：`create_product(name, unit, default_price_cents, pinyin_initials="", *, safety_stock=None, brand=None)`、`update_product_record(product_id, name, unit, default_price_cents, *, safety_stock=None, brand=...)`；开单明细 payload 只认 `product_name`。
- 开单页商品控件是**单框**：销售/退货 `.picker-name-input`（手输 + 名称建议，未命中交给后端自动建档）；拿货/退拿货 `.picker-name` 下拉（必须选定已建档 ID）。`static/order-product-picker.js` 的 `mountRow`/`mountHybrid` 都按单框实现。
- 分析中心**没有型号维度**：无「型号 / 未填写型号」控件、无 `spec` 查询参数、无「型号：…」chip、表格无型号列。
- 导入：模板表头 = `商品名称 / 品牌 / 单位 / 默认价 / 期初数量 / 期初成本（元）/ 备注`（具名常量 `PRODUCT_HEADERS_TEMPLATE`，不要再写 `PRODUCT_HEADERS[i]` 魔法索引）。**含「型号」列的旧文件按整表头精确匹配直接报「表头错误，应为：…」**——这是预期行为（明确报错，不静默丢数据），不要「修」成兼容。
- 商品侧「待补全（型号为空）」已**整体删除**（删列后该条件恒为真）；客户侧待补全（`origin='order'` 且无联系方式）保留。

## 升级迁移的安全边界（`db.py::migrate_merge_product_spec_into_name`）

顺序不可调换：

1. **只读预检**（门店机器上先跑 `tools/precheck_product_identity_merge.py <erp.db>`，退出码 0/1/2）：若合并后撞名（库里既有 `闸阀DN50` 又有 `闸阀`+`DN50`），或名称与型号都为空 → **中止且不做任何写入、不产生备份**，列出冲突清单交用户裁决，绝不静默合并。
2. **写库前自动整库备份** `create_backup("pre_merge_product_spec")`（`erp.db` + `config.json` + 商品图片 + 旧账存档）。
3. 四表整体重建（SQLite 不能删列）：沿用库内 DDL 去掉 `spec` 行 → 复制数据并把 `name`/`product_name` 拼上型号 → DROP + RENAME → 重建非唯一索引 + 新唯一索引 → 保 `sqlite_sequence` 高位。
4. **`PRAGMA foreign_key_check` 不通过 → 整笔回滚**。
5. 接线点：`init_db()` 中与 `migrate_purchase_return_decoupling()` 并列、位于 `with get_db()` **之前**（只写函数不接线 = 从不执行）。
6. 与既有迁移同规矩：`schema_version ≥ CURRENT_SCHEMA_VERSION` 的库直接跳过（不对更新版本程序建的库动 DDL）；幂等，第二次 `init_db()` 直接返回。
7. **`SCHEMA_SQL` 必须同批删四处 `spec` 列**，否则新装机又建出带 spec 的表，与迁移后的老库分叉出两种 schema（且不报错，只在后续查询时 `no such column`）。验收要补一条：新建空库启动后四表 `PRAGMA table_info` 无 `spec`，且与老库迁移后逐列一致。

## 迁移验收（每次改动这条链路都要重跑）

- 隔离库：迁移前后 `products`/`order_items` 行数不变，`orders.total_amount_cents` 逐行不变，库存三表（`inventory_postings`/`product_inventory_state`/`inventory_initializations`）逐行不变，`PRAGMA foreign_key_check` 为空。
- 正式库**副本**：把 `erp.db` 连 `-wal`/`-shm` 一起复制到 scratch 上跑迁移，正式库本体只读（跑前跑后 size/mtime 不变、`-wal` 仍 0 字节）。**本机副本 0 冲突不代表门店数据**——门店升级前必须现场跑预检。
- 夹具要模拟**真 v13 库**：`spec` 列必须在建表语句里**独占一行**（迁移按行定位该列），且 `schema_version` 要退回 13，否则迁移会按「更新版本」跳过、测试变成假通过。

## 测试适配经验（同类「列合并/去列」改动可复用）

- 大面积改写测试调用签名时，用**一次性 AST codemod**（按 `lineno/col_offset` 定位、跳过语法不过的文件）比手改 50 个文件可靠；但**只做机械改写**，语义判断必须逐文件复核。
- 两个相邻字符串字面量的隐式拼接要注意：`create_product("  消防水带  ", "  DN65  ")` 直接拼成 `"  消防水带    DN65  "` 会与应用端 `TRIM(name)||TRIM(spec)` 语义不符 → 折叠成单字面量时两侧都要 `strip()`。
- 模块属性调用（`accounting.create_product(...)`）不会被只匹配 `ast.Name` 的 codemod 覆盖，容易漏。
- 纪律：**改断言不删测试**。「行为被有意删除」的用例改写成钉住新行为并加「决策变更」注释；用「测试函数数量无减少 + assert 行数无净减少 + 无新增 skip/xfail」做机械自检。
