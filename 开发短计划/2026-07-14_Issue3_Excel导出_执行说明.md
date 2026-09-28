# Issue #3 Excel 数据导出 — 执行说明

> 历史执行记录：仅描述该次任务的需求与当时验收状态，不代表当前待办。当前产品行为以 `docs/需求/消防器材店ERP系统-需求文档.md`、源码和测试为准。

- 分支：`feature/issue-3-excel-export`
- 基线：`main` @ v0.5.0（`5a17e63`）
- 范围：客户 / 商品 / 销售单 / 退货单 `.xlsx` 导出；不改 PDF 汇总、不改导入模板语义、不发版

## URL

| 导出 | URL |
|---|---|
| 客户 | `GET /customers/export.xlsx`（可选 `?q=`） |
| 商品 | `GET /products/export.xlsx`（可选 `?q=`） |
| 销售单 | `GET /orders/export/sales.xlsx` |
| 退货单 | `GET /orders/export/returns.xlsx` |

## 筛选

- 客户/商品：跟随列表当前 `q`（按钮带查询参数）
- 销售单/退货单：跟随单据页客户、日期模式与日期范围；**类型由按钮固定**（销售单导出只出 sale，退货单导出只出 return），忽略页面 `order_type` 下拉，避免互相冲突
- 均只导出 `deleted_at IS NULL` 的主数据/单据；单据用明细 `JOIN`，无明细的空单不会出现

## 结构

- 单据导出：**一行一个明细**
- 单号/日期/客户/类型/状态/备注/单据合计在每行重复，便于 Excel 透视

## 金额

- 库内分 → `cents_to_yuan` 导出为两位小数字符串（元）
- 退货单 `单据合计（元）` 按库内符号导出（退货合计为负）

## 测试

```bash
PYTHONPATH=app pytest tests/test_excel_export.py tests/test_master_data_import.py tests/test_orders_summary_export.py -q
PYTHONPATH=app pytest tests/ -q
```
