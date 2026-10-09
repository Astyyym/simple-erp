"""Batch B：客户/商品导入扩列、预检预览与总额自检（共享逻辑）。

设计要点（见 开发短计划/2026-10-08_旧数据迁移承接与备份恢复_执行计划.md §五 Batch B）：
- **未确认零写入**：预览阶段只解析与校验，绝不落库；落库只在确认阶段发生。
- 预览态经隐藏域（JSON）回传，服务端**二次校验**后才写；不落临时文件。
- 表头**同时登记新旧**，旧模板仍可导入（单位回落「个」、无期初）。
- 同名默认「跳过」；可选「更新」只覆盖可空档案字段，不动业务流水。
- 命中**回收站**同名时明确提示，不静默跳过（B-8）。
- 期初数量/成本走 `initialize_product`（其内部自开事务），用「批次号+行号」作幂等键。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from erp.utils.importing import MAX_DISPLAYED_ERRORS, normalized_name, price_to_cents

# ---- 表头登记（B-3：新旧同时兼容） ----
CUSTOMER_HEADERS = (
    ["客户名称"],
    ["客户名称", "电话", "地址", "期初余额（元）", "备注"],
    ["客户名称", "电话", "地址", "期初余额", "备注"],
)
PRODUCT_HEADERS = (
    ["商品名称", "型号", "价格"],
    ["商品名称", "价格"],
    ["商品名称", "型号", "品牌", "单位", "默认价", "期初数量", "期初成本（元）", "备注"],
    ["商品名称", "型号", "品牌", "单位", "默认价", "期初数量", "期初成本", "备注"],
    # E-2 之前的表头仍可导入（无品牌列 → 品牌留空）。
    ["商品名称", "型号", "单位", "默认价", "期初数量", "期初成本（元）", "备注"],
    ["商品名称", "型号", "单位", "默认价", "期初数量", "期初成本", "备注"],
)


@dataclass
class PreviewRow:
    line_number: int
    action: str  # add | skip | error | update
    identity: str
    reason: str = ""
    payload: dict = field(default_factory=dict)


@dataclass
class ImportPreview:
    kind: str
    rows: list[PreviewRow] = field(default_factory=list)
    truncated_errors: int = 0
    batch_id: str = ""

    @property
    def added(self) -> int:
        return sum(1 for r in self.rows if r.action == "add")

    @property
    def updated(self) -> int:
        return sum(1 for r in self.rows if r.action == "update")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.rows if r.action == "skip")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.rows if r.action == "error")

    @property
    def errors(self) -> list[str]:
        return [f"第{r.line_number}行：{r.reason}" for r in self.rows if r.action == "error"][:MAX_DISPLAYED_ERRORS]

    def to_json(self) -> str:
        return json.dumps(
            {
                "kind": self.kind,
                "batch_id": self.batch_id,
                "rows": [
                    {"line_number": r.line_number, "action": r.action, "identity": r.identity,
                     "reason": r.reason, "payload": r.payload}
                    for r in self.rows
                ],
                "truncated_errors": self.truncated_errors,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str) -> "ImportPreview":
        data = json.loads(text)
        preview = cls(kind=data.get("kind", ""))
        preview.truncated_errors = int(data.get("truncated_errors", 0))
        preview.batch_id = str(data.get("batch_id", ""))
        for raw in data.get("rows", []):
            preview.rows.append(
                PreviewRow(
                    line_number=int(raw["line_number"]),
                    action=str(raw["action"]),
                    identity=str(raw.get("identity", "")),
                    reason=str(raw.get("reason", "")),
                    payload=raw.get("payload", {}) or {},
                )
            )
        return preview


def _money_to_cents(value, *, field_label: str, allow_blank: bool = True) -> int | None:
    text = "" if value is None else str(value).strip()
    if text == "":
        if allow_blank:
            return None
        raise ValueError(f"{field_label}不能为空")
    # 容忍常见的「¥」「元」前缀与千分位逗号。
    cleaned = text.replace("¥", "").replace("￥", "").replace("元", "").replace(",", "").strip()
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_label}格式无效（应填数字，例如 12.50）") from None
    if not amount.is_finite():
        raise ValueError(f"{field_label}格式无效")
    return int((amount * 100).to_integral_value())


def _quantity_text(value) -> str:
    return "" if value is None else str(value).strip()


def _header_index(headers: list[str], *names: str) -> int | None:
    for i, h in enumerate(headers):
        if h in names:
            return i
    return None


def _cell(row: list, index: int | None):
    if index is None or index >= len(row):
        return None
    return row[index]


def build_customer_preview(headers, rows, existing_names: set[str], soft_deleted_names: set[str], *, same_name: str = "skip") -> ImportPreview:
    """按**列名**映射，兼容旧单列表头与新五列表头（不依赖列位置）。"""
    preview = ImportPreview(kind="customers")
    idx_name = _header_index(headers, "客户名称")
    idx_phone = _header_index(headers, "电话")
    idx_address = _header_index(headers, "地址")
    idx_opening = _header_index(headers, "期初余额（元）", "期初余额")
    seen: set[str] = set()
    for line_number, row in rows:
        name = normalized_name(_cell(row, idx_name))
        if not name:
            preview.rows.append(PreviewRow(line_number, "error", "", "客户名称不能为空"))
            continue
        phone = normalized_name(_cell(row, idx_phone))
        address = normalized_name(_cell(row, idx_address))
        try:
            opening = _money_to_cents(_cell(row, idx_opening), field_label="期初余额")
        except ValueError as exc:
            preview.rows.append(PreviewRow(line_number, "error", name, str(exc)))
            continue
        payload = {"name": name, "phone": phone, "address": address,
                   "opening_balance_cents": opening or 0}
        if name in seen:
            preview.rows.append(PreviewRow(line_number, "skip", name, "文件内重复"))
            continue
        seen.add(name)
        if name in soft_deleted_names:
            # B-8：回收站占名必须明确提示，不能静默跳过。
            preview.rows.append(PreviewRow(line_number, "error", name, "该名称已被回收站记录占用，请先到回收站恢复或清理"))
            continue
        if name in existing_names:
            if same_name == "update":
                preview.rows.append(PreviewRow(line_number, "update", name, "已存在，将更新联系方式/期初", payload))
            else:
                preview.rows.append(PreviewRow(line_number, "skip", name, "同名已存在"))
            continue
        preview.rows.append(PreviewRow(line_number, "add", name, "", payload))
    return preview


def build_product_preview(headers, rows, existing_identities: set[tuple[str, str]], soft_deleted_identities: set[tuple[str, str]], *, same_name: str = "skip") -> ImportPreview:
    """按**列名**映射，兼容「商品名称,价格」/「商品名称,型号,价格」/新旧宽表头（含可选品牌列）。"""
    preview = ImportPreview(kind="products")
    idx_name = _header_index(headers, "商品名称")
    idx_spec = _header_index(headers, "型号")
    idx_brand = _header_index(headers, "品牌")
    idx_unit = _header_index(headers, "单位")
    idx_price = _header_index(headers, "价格", "默认价")
    idx_qty = _header_index(headers, "期初数量")
    idx_cost = _header_index(headers, "期初成本（元）", "期初成本")
    seen: set[tuple[str, str]] = set()
    for line_number, row in rows:
        name = normalized_name(_cell(row, idx_name))
        if not name:
            preview.rows.append(PreviewRow(line_number, "error", "", "商品名称不能为空"))
            continue
        spec = normalized_name(_cell(row, idx_spec))
        # E-2：品牌可空；空单元格与「没这一列」都落成空串（写入时统一转 NULL）。
        brand = normalized_name(_cell(row, idx_brand))
        unit = normalized_name(_cell(row, idx_unit)) or "个"
        price_value = _cell(row, idx_price)
        qty_text = _quantity_text(_cell(row, idx_qty))
        cost_value = _cell(row, idx_cost)
        identity = (name, spec)
        label = f"{name} {spec}".strip()
        if identity in seen:
            preview.rows.append(PreviewRow(line_number, "skip", label, "文件内重复"))
            continue
        seen.add(identity)
        try:
            price_cents = price_to_cents(price_value)
        except ValueError as exc:
            preview.rows.append(PreviewRow(line_number, "error", label, str(exc)))
            continue
        try:
            cost_cents = _money_to_cents(cost_value, field_label="期初成本")
        except ValueError as exc:
            preview.rows.append(PreviewRow(line_number, "error", label, str(exc)))
            continue
        try:
            has_qty = bool(qty_text) and Decimal(qty_text) > 0
        except (InvalidOperation, ValueError):
            preview.rows.append(PreviewRow(line_number, "error", label, "期初数量格式无效"))
            continue
        if has_qty and cost_cents is None:
            # 有数量必须有成本（缺成本不静默）。
            preview.rows.append(PreviewRow(line_number, "error", label, "填了期初数量就必须填期初成本（元）"))
            continue
        payload = {"name": name, "spec": spec, "brand": brand, "unit": unit, "default_price_cents": price_cents,
                   "quantity": qty_text, "cost_cents": cost_cents}
        if identity in soft_deleted_identities:
            preview.rows.append(PreviewRow(line_number, "error", label, "该名称+型号已被回收站记录占用，请先到回收站恢复或清理"))
            continue
        if identity in existing_identities:
            if same_name == "update":
                preview.rows.append(PreviewRow(line_number, "update", label, "已存在，将更新单位/默认价", payload))
            else:
                preview.rows.append(PreviewRow(line_number, "skip", label, "同名同型号已存在"))
            continue
        preview.rows.append(PreviewRow(line_number, "add", label, "", payload))
    return preview


def customer_totals(preview: ImportPreview) -> dict:
    """B-7：导入总额自检——客户欠款合计（新增+更新后）。"""
    total = sum(int(r.payload.get("opening_balance_cents", 0)) for r in preview.rows if r.action in ("add", "update"))
    return {"opening_balance_total_cents": total}


def product_totals(preview: ImportPreview) -> dict:
    """B-7：商品期初库存金额合计。"""
    total = 0
    for r in preview.rows:
        if r.action not in ("add", "update"):
            continue
        qty = _quantity_text(r.payload.get("quantity"))
        cost = r.payload.get("cost_cents")
        if qty and cost is not None:
            total += int((Decimal(qty) * Decimal(int(cost))).to_integral_value())
    return {"initial_cost_total_cents": total}
