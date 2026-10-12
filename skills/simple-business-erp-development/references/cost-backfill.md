# 历史成本人工补录（销售单）— 2026-10-11 确认 / 2026-10-12 实现

缺成本的旧销售单（先卖后补路径、零建档期销售）在分析与退货路径上都是死路：
毛利算不出、成本诊断「未覆盖」、退货来源下拉选不到、关联退货硬拒。
本能力提供「作废重录」之外的第二条纠错路径：选一张老单、人工填成本、
系统补上快照并留痕。

## 核心取舍（不可偏离）

**只补利润、不动库存。** 补录只写 `order_items.unit_cost_micro` /
`cost_total_micro` / `cost_source` 与一条审计；`inventory_postings`、
`product_inventory_state`、`inventory_initializations` 三表逐行不变。
改动若需要写库存流水或改库存状态 → 停，回用户确认。

**零改聚合。** 分析与利润服务本就按 `cost_total_micro IS NULL` 判未知成本，
数据落库即自动生效。需要改聚合口径才能生效，说明设计有问题。

## 数据层

- `order_items.cost_source TEXT`，幂等加列（`db.py::ensure_order_item_cost_source`），
  **不推进 `CURRENT_SCHEMA_VERSION`**——对齐 `ensure_master_data_origin_fields`、
  `ensure_product_brand_field`、`ensure_initialization_cost_source` 的既有惯例。
  `SCHEMA_SQL` 的 `order_items` 定义同步加列；`init_db()` 迁移链插在
  `ensure_order_item_cost_fields` 之后。
- 语义：`NULL` = 系统写入的销售时真实快照（**不可覆盖**）；
  `'backfill'` = 人工补录（**可再修正**）。旧数据留空即按真实快照处理。

## 服务层 `app/erp/services/cost_backfill.py`

`backfill_order_costs(order_id, entries, reason) -> {"updated": n}`

准入（`BEGIN IMMEDIATE` 内校验）：单据存在且未软删、`order_type='sale'`、
`status IN ('saved','printed')`（作废/草稿各有中文原因）、原因非空。

逐行校验：
- `item_id` 必须属于该单（跨单明细拒绝）；
- 行当前 `cost_total_micro IS NULL` 或 `cost_source='backfill'`，
  否则「该行已有销售时成本，不能补录」；
- **已有有效退货时拒绝再修正**（`_has_live_return`）——原行成本改了、
  退货累计成本不会重算，已全部退完的单会留下永久差额。用户 2026-10-11
  拍板维持「拒绝」，不放宽为「允许 + 警告」。
- 金额：单位成本或总成本二选一（单位成本优先）；非负、有限、≤6 位小数 → micro 整数。
  与期初成本 `_cost_micro` 同口径。
- 审计 `log_action("backfill_order_cost", "order", order_id, ..., conn=conn)`
  **必须传 `conn=conn`**，与 UPDATE 同一事务，避免「补录成功、审计丢失」。

## 路由与页面

| 路由 | 行为 |
|---|---|
| `GET /orders/<id>/cost_backfill` | 补录表单页（`orders/cost_backfill.html`）。真实快照行只读；无成本/补录行可填。**提交前必须披露**「补录后本单将不能再重编辑」——补录前该单恰恰**是**可编辑的，提交后才失去编辑能力，所以披露写在补录页说明里，不能只写在编辑按钮的置灰原因里。 |
| `POST /orders/<id>/cost_backfill` | 服务层 → 成功 302 回详情页 `?backfilled=1`；失败就地中文错误页。 |

详情页（`orders/detail.html`）：
- 符合条件的单显示「补录历史成本」按钮（`backfillable`）；
- 行表增加「成本来源」列（人工补录 / 销售时快照 / 缺成本徽标）；
- 成功提示条读 `request.args.get('backfilled')` **就地渲染，不依赖 flash**
  （详情页没有 flash 渲染区）；
- 含补录行时毛利提示文案由「按销售时成本快照计算」改为「含人工补录成本」。

`_document_editable` / `_document_edit_block_reason` 扩展一个 `has_backfill` 形参
（默认 `False`，签名向后兼容），补录单不可重编辑；调用点：列表、当天历史、详情、
编辑路由 GET/POST 双拦截（POST 也必须拦，否则可绕过）。

## 发现路径

- 列表缺成本销售单挂「缺成本」徽标（`has_missing_cost`，union SQL 的 sale 分支
  加 EXISTS 子查询）；
- 筛选 `cost=missing`；**筛选开启时非销售分支（退货/拿货/退拿货）条件固定 `1=0`**，
  语义写死；
- `order_filter_chips(*, customer_q, order_types, order_statuses, cost_missing=False)`
  多一个 chip。

## 连锁效果（要写进补录页说明）

- 补录后该单出现在**退货来源下拉**（`_return_source_orders` 的 EXISTS 条件自动满足）；
- 关联退货时：商品**已启用库存** → 退货按补录成本入库（期初填的是启用时实际库存，
  退货是启用后真实回库，逻辑自洽）；商品**未启用** → 仍按既有规则拒绝；
- 分析页：该单离开「未覆盖」范围，毛利参与计算。

## 明确排除

退货单/拿货单/退拿货单补录；批量补录；改分析聚合或新增「人工补录毛利」分列口径；
写库存流水/改库存状态/改期初；自动猜测成本（按商品当前均价回填）——一律人工填写。
