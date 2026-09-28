# Excel 数据导出（Issue #3 实测，v0.5.0 基线）

与「导出汇总表 PDF」完全不同：本能力是业务主数据/单据 `.xlsx` 下载。

## 模块与路由

- 新建 `app/erp/utils/exporting.py`（勿把大段 openpyxl 堆进 routes）
- 复用 `importing.excel_template` 的响应风格：`Workbook` → `BytesIO` → `send_file(..., as_attachment=True, mimetype=xlsx)`
- 金额一律 `cents_to_yuan`；库内分，表内元（两位小数字符串）
- 仅 `deleted_at IS NULL`；单据 `JOIN order_items`（无明细空单不导出）

| 导出 | 路由 | 文件名 |
|---|---|---|
| 客户 | `GET /customers/export.xlsx` | `客户导出_YYYYMMDD.xlsx` |
| 商品 | `GET /products/export.xlsx` | `商品导出_YYYYMMDD.xlsx` |
| 销售单 | `GET /orders/export/sales.xlsx` | `销售单导出_YYYYMMDD.xlsx` |
| 退货单 | `GET /orders/export/returns.xlsx` | `退货单导出_YYYYMMDD.xlsx` |

列表页按钮：客户/商品「导出Excel」；单据管理「导出销售单Excel」「导出退货单Excel」。保留「导出汇总表」PDF 入口不动。

## 字段（最小可用）

- 客户：名称、电话、地址、期初余额（元）、备注、创建/更新时间
- 商品：名称、规格、单位、默认价（元）、备注、使用次数、创建/更新时间
- 单据（**一行一明细**）：单号、日期、客户、单据类型、状态、品名、规格、单位、数量、单价（元）、金额（元）、单据备注、单据合计（元）
  - 单头字段在每行重复，便于透视
  - 退货单 `total_amount_cents` 为负 → 合计列按符号导出（如 `-20.00`）；明细 `subtotal_cents` 仍为正

## 筛选语义（实测约定）

- 客户/商品：跟随列表 `q`
- 销售/退货：跟随客户 + 日期模式/范围（与 `list_orders` 同一套 `_resolve_date_range`）
- **类型由路由固定**（sales 只出 sale，returns 只出 return），**忽略**页面 `order_type` 下拉，避免与专用按钮冲突
- 前端 `syncExportLink`：PDF 汇总删 `order_type`；两个 xlsx 按钮同步客户/日期参数，同样删 `order_type`

## 测试要点

`tests/test_excel_export.py`：

1. 构造客户/商品/销售明细两行/退货一行；软删主数据不得出现
2. 四接口 200 + xlsx mimetype + attachment
3. `openpyxl.load_workbook(BytesIO(response.data))` 断言中文表头与金额
4. With permanent no-login active, business pages and exports are accessible without a session; `/health` returns `auth: disabled`.
5. 回归：`test_master_data_import` + `test_orders_summary_export` 不得挂

## 禁止

- 不要把导出做成打印预览/HTML/PDF 变体
- 不要改导入模板表头语义
- 不要引入 pandas；requirements 已有 openpyxl 即可
- 单 issue 交付：分支 + 测试 + `Fixes #N` commit；默认不 VERSION / 不 Release
