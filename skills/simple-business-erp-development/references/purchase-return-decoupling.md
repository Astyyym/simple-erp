# 退拿货单解耦与手工开单（2026-10-06，schema v13）

## 单号规则（2026-10-06 修订）

- 销售/退货共用 `MD` + 8 位业务日期 + 4 位日流水；**拿货与退拿货共用同一套 `NH` 号池**（同一天、同一 4 位流水），对齐「销售+退货共用 MD」的规则。
- 取号实现集中在 `app/erp/utils/order_numbering.py`（`next_nh_order_no` 占用号、`peek_nh_order_no` 只预览不占用）；`routes/purchases.py` 的 `next_purchase_no` / `next_return_no` 都是它的薄封装，两页开单只读单号与 `/purchases/api/next_order_no` 一律走 `peek`，避免刷新预览页就吃掉号。
- **历史 TN 兼容**：旧退拿货单号 `TN...` 不重写；取号时会把同一天已有 `TN` 流水也算进最大值，避免新 NH 号与历史单号重复。
- 流水表 `purchase_number_sequences` 为共用的唯一流水来源；旧的 `purchase_return_number_sequences` 仅为兼容既有库保留，不再写入。
- 达到 9999 时明确拒绝创建，不生成五位流水。

## 当前规则

- 销售单、退货单、拿货单、退拿货单四类互相独立：退货单不依赖销售单，退拿货单不依赖原拿货单。
- 四类开单的商品选择统一为“产品名称 → 型号”两级原生下拉，共用 `app/erp/static/order-product-picker.js`；目录以 JSON（`#productCatalog`）喂数据，选项文本用 `textContent` 注入，不拼 HTML。未启用库存的商品在型号项标注“未启用，仅可保存草稿”。
- 退拿货手工开单：往来对象 + 业务日期 + 商品行 + 手输单价；库存出库成本按**当前移动平均成本**精确分摊（`inventory.post_typed_purchase_return`）；缺货 / 未启用 / 未知商品整单拒绝且不产生半成品。
- 旧带来源行的退拿货单只读兼容且可作废；`void/delete/restore/edit_notes` 与来源无关（作废按 `posting_seq` join 库存流水反向）。
- `purchase_returns.create/update_draft/finalize` 支持双模：传 `source_order_id` 走旧来源路径，否则走手工 `items=[{product_id, quantity, unit_price_yuan}]` 路径。

## schema v13 迁移

- `purchase_return_orders.source_order_id`、`purchase_return_items.source_item_id` 放宽为可空；原复合 `UNIQUE(purchase_return_id, source_item_id)` 改为**部分唯一索引**（`WHERE source_item_id IS NOT NULL`），来源行的“不重复”约束保留。
- `db.migrate_purchase_return_decoupling()` 在 `init_db` 主事务**之前**，用独立连接 `PRAGMA foreign_keys=OFF` + `legacy_alter_table=ON` 重建两张表，逐列原样保留旧行；先检查 `schema_version`，较新版本直接返回让 `apply_schema` 抛“不支持”。
- 迁移幂等：`source_order_id` 的 `notnull==0` 即视为已迁移。

## 易错点

- `inventory.post_typed_purchase_return` 的入参是**已放大的 3 位小数整数** `quantity_3dp`，不要再经 `_scaled_quantity` 二次放大（会把 2 当成 2,000,000，误报“库存不足”）。它返回的 snapshot 含 `posting_seq`，手工路径必须写入 `purchase_return_items.posting_seq`（作废靠它匹配库存流水）。
- 手工单价复用 `inventory._price_cents`；数量解析用 `_scaled_quantity`（这里接收人类可读数量）。
