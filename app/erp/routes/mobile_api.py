"""手机端 **JSON 接口** `/m/api/*`：给原生安卓客户端用。

为什么要有这一层（而不是让安卓去解析 HTML）：

1. **口径只能有一份**。余额、退货负数、状态过滤、四类分组这些小票级细节，
   在 `services/readonly_queries.py` 里已经写死并有对拍测试。让安卓端自己算一遍
   = 两份实现，迟早分叉，而且分叉时不会有任何测试失败。
2. **格式化也在服务端做**。`money_short` / `money_exact` / `quantity_text` 的
   千分位、负号位置、小数位规则都在 `utils/mobile_format.py`；接口直接把
   「已经排好版的字符串」发出去，安卓只负责画，不负责算。
3. **离线缓存要有稳定的版本号**。每条响应带 `schema`，客户端缓存键与
   反序列化都认它，字段改了旧缓存能识别并丢弃，而不是崩在解析上。

约束（与 HTML 蓝图同一套，不放松）：

- **只 GET**。`POST /m/api/*` 一律 405，唯一例外是配对提交 `POST /m/api/pair`。
- **所有查询走 `mode=ro` 连接**（`readonly_queries` 内部保证）。
- **蓝图外仍然 404**：本蓝图只挂在第二监听的独立 app 上。
- 响应**不缓存**（`no-store`），离线缓存由客户端自己负责，服务端不当缓存层。
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from erp.services import mobile_pairing, readonly_queries
from erp.utils.filter_chips import ORDER_TYPE_LABELS, ORDER_TYPE_ORDER
from erp.utils.mobile_format import (
    money_exact,
    money_short,
    month_day_label,
    quantity_text,
    short_date,
    timestamp_label,
    weekday_label,
)

# 客户端缓存键里带它：字段增删时改这里，旧缓存会被识别并丢弃。
API_SCHEMA = 1

mobile_api_bp = Blueprint("mobile_api", __name__, url_prefix="/m/api")

# 不过设备门的端点：配对页本身与配对提交。
PAIRING_ENDPOINTS = {"mobile_api.pair_status", "mobile_api.pair_submit"}


def _envelope(**payload):
    """统一信封：客户端只认 `ok`，不用按接口各写一套解析。"""
    body = {"schema": API_SCHEMA, "ok": True}
    body.update(payload)
    return jsonify(body)


def _error(message: str, status: int = 400, **extra):
    body = {"schema": API_SCHEMA, "ok": False, "error": message}
    body.update(extra)
    response = jsonify(body)
    response.status_code = status
    return response


@mobile_api_bp.before_request
def guard_api_readonly():
    """设备门 + 拒非 GET。与 HTML 蓝图同一套判定，共用 `mobile_pairing`。

    与 HTML 蓝图的唯一差别：这里**不跳转**到配对页（那是 HTML 的行为）。
    原生客户端要的是一个明确的 401 + `needs_pairing`，好据此切到配对界面。
    """
    if request.endpoint in PAIRING_ENDPOINTS:
        return None
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        return _error("手机端是只读接口，不支持这个操作。", 405)
    device = mobile_pairing.device_from_cookie(request.cookies.get(mobile_pairing.COOKIE_NAME))
    if device is None:
        return _error("设备未配对。", 401, needs_pairing=True)
    mobile_pairing.remember_success(device, request.remote_addr or "")
    return None


# ---------------------------------------------------------------------------
# 配对（POST 是全局唯一例外，只写 mobile_devices.json，绝不写 erp.db）
# ---------------------------------------------------------------------------

@mobile_api_bp.get("/pair/status")
def pair_status():
    """未配对时客户端靠它决定要不要显示配对页；已配对时返回设备信息。"""
    device = mobile_pairing.device_from_cookie(request.cookies.get(mobile_pairing.COOKIE_NAME))
    if device is None:
        return _envelope(paired=False, lock_seconds=mobile_pairing.lock_remaining_seconds())
    return _envelope(
        paired=True,
        device={
            "label": device.get("label", ""),
            "paired_at": timestamp_label(device.get("paired_at", "")),
        },
    )


@mobile_api_bp.post("/pair")
def pair_submit():
    """原生端配对：与 HTML 的 `POST /m/pair` 同一套校验，返回 JSON + Set-Cookie。"""
    payload = request.get_json(silent=True) or request.form
    code = str(payload.get("code") or "")
    label = str(payload.get("label") or "").strip()
    # 走 JSON 接口的默认就是原生客户端；`platform` 允许显式覆盖（留了余地，
    # 但服务端只认 app/browser 两个值，见 `normalize_platform`）。
    platform = str(payload.get("platform") or mobile_pairing.PLATFORM_APP)
    cookie_value, error = mobile_pairing.verify_pair_code(
        code,
        label=label or "手机 App",
        ip=request.remote_addr or "",
        platform=platform,
    )
    if error:
        return _error(error, 400, lock_seconds=mobile_pairing.lock_remaining_seconds())
    response = jsonify({"schema": API_SCHEMA, "ok": True, "paired": True})
    response.set_cookie(
        mobile_pairing.COOKIE_NAME,
        cookie_value,
        max_age=400 * 24 * 3600,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


# ---------------------------------------------------------------------------
# 今日概览
# ---------------------------------------------------------------------------

@mobile_api_bp.get("/today")
def today():
    overview = readonly_queries.today_overview()
    types = [
        {
            "type": entry["type"],
            "label": ORDER_TYPE_LABELS[entry["type"]],
            "count": entry["count"],
            "amount_cents": entry["amount_cents"],
            "amount_text": money_short(entry["amount_cents"]),
        }
        for entry in overview["types"]
    ]
    events = [
        {
            "order_type": event["order_type"],
            "type_label": ORDER_TYPE_LABELS.get(event["order_type"], ""),
            "order_no": event["order_no"],
            "customer_name": event["customer_name"],
            "amount_cents": event["amount_cents"],
            "amount_text": money_short(event["amount_cents"]),
            "source_id": event["source_id"],
            "time": event["time"],
        }
        for event in overview["events"]
    ]
    return _envelope(
        today={
            "date": overview["date"],
            "date_label": month_day_label(overview["date"]),
            "weekday": weekday_label(overview["date"]),
            "document_count": overview["document_count"],
            "types": types,
            "net_sale_cents": overview["net_sale_cents"],
            "net_sale_text": money_short(overview["net_sale_cents"]),
            "net_purchase_cents": overview["net_purchase_cents"],
            "net_purchase_text": money_short(overview["net_purchase_cents"]),
            "events": events,
        }
    )


# ---------------------------------------------------------------------------
# 客户
# ---------------------------------------------------------------------------

@mobile_api_bp.get("/customers")
def customers():
    q = (request.args.get("q") or "").strip()
    only_debt = (request.args.get("filter") or "") == "debt"
    sort = (request.args.get("sort") or "").strip()
    if sort not in {"pinyin", "debt"}:
        sort = "pinyin"
    # 正倒序：空字符串 = 用各排序的历史默认方向（拼音 A→Z、欠款多→少）。
    order = (request.args.get("order") or "").strip()
    if order not in {"asc", "desc"}:
        order = ""
    result = readonly_queries.list_customers(q, only_debt=only_debt, sort=sort, order=order)
    items = [
        {
            "id": customer["id"],
            "name": customer["name"],
            "pinyin": customer["pinyin"],
            "phone": customer["phone"],
            "balance_cents": customer["balance_cents"],
            "balance_text": money_short(customer["balance_cents"]),
        }
        for customer in result["customers"]
    ]
    return _envelope(
        customers=items,
        truncated=result["truncated"],
        query=q,
        sort=sort,
        order=order,
        only_debt=only_debt,
    )


@mobile_api_bp.get("/customers/<int:customer_id>")
def customer_detail(customer_id: int):
    detail = readonly_queries.customer_detail(customer_id)
    if detail is None:
        return _error("这个客户不存在，可能已被删除。", 404)
    price_list = readonly_queries.customer_price_list(customer_id) or {
        "prices": [],
        "price_count": 0,
        "truncated": False,
    }
    ledger_labels = {**ORDER_TYPE_LABELS, "payment": "收款", "adjustment": "账务调整"}
    entries = [
        {
            "type": entry.get("type", ""),
            "type_label": ledger_labels.get(entry.get("type"), entry.get("type", "")),
            "date": entry.get("date", ""),
            "date_label": month_day_label(entry.get("date", "")),
            "amount_cents": int(entry.get("amount_cents") or 0),
            "amount_text": money_short(int(entry.get("amount_cents") or 0)),
            "description": entry.get("description", ""),
        }
        for entry in detail["entries"]
    ]
    documents = [
        {
            "id": document["id"],
            "order_type": document["order_type"],
            "type_label": ORDER_TYPE_LABELS.get(document["order_type"], ""),
            "order_no": document["order_no"],
            "business_date": document["business_date"],
            "date_label": short_date(document["business_date"]),
            "amount_cents": document["amount_cents"],
            "amount_text": money_short(document["amount_cents"]),
            "time_label": document.get("time_label", ""),
        }
        for document in detail["documents"]
    ]
    prices = [
        {
            "product_id": price["product_id"],
            "product_name": price["product_name"],
            "unit": price["unit"],
            "price_cents": price["price_cents"],
            "price_text": money_short(price["price_cents"]),
            "updated_label": price["updated_label"],
        }
        for price in price_list["prices"]
    ]
    return _envelope(
        customer={
            "id": detail["customer"]["id"],
            "name": detail["customer"]["name"],
            "pinyin": detail["customer"]["pinyin"],
            "phone": detail["customer"]["phone"],
            "address": detail["customer"]["address"],
            "opening_balance_cents": detail["opening_balance_cents"],
            "current_balance_cents": detail["current_balance_cents"],
            "current_balance_text": money_exact(detail["current_balance_cents"]),
            "change_cents": detail["change_cents"],
            "change_text": money_short(detail["change_cents"]),
        },
        entries=entries,
        documents=documents,
        documents_truncated=detail["documents_truncated"],
        prices=prices,
        price_count=price_list["price_count"],
        prices_truncated=price_list["truncated"],
    )


@mobile_api_bp.get("/customers/<int:customer_id>/prices")
def customer_prices(customer_id: int):
    price_list = readonly_queries.customer_price_list(customer_id)
    if price_list is None:
        return _error("这个客户不存在，可能已被删除。", 404)
    prices = [
        {
            "product_id": price["product_id"],
            "product_name": price["product_name"],
            "unit": price["unit"],
            "price_cents": price["price_cents"],
            "price_text": money_short(price["price_cents"]),
            "updated_label": price["updated_label"],
        }
        for price in price_list["prices"]
    ]
    return _envelope(
        customer=price_list["customer"],
        prices=prices,
        price_count=price_list["price_count"],
        truncated=price_list["truncated"],
    )


# ---------------------------------------------------------------------------
# 单据
# ---------------------------------------------------------------------------

@mobile_api_bp.get("/orders")
def orders():
    order_type = (request.args.get("order_type") or "").strip()
    if order_type not in ORDER_TYPE_LABELS:
        order_type = ""
    customer_id = request.args.get("customer_id", type=int) or 0
    customer_brief = readonly_queries.customer_brief(customer_id) if customer_id else None
    if customer_brief is None:
        customer_id = 0
    date_range = readonly_queries.resolve_mobile_range(request.args)
    result = readonly_queries.list_documents(
        order_type=order_type,
        start_date=date_range["start_date"],
        end_date=date_range["end_date"],
        customer_id=customer_id or None,
    )
    documents = [
        {
            "id": document["id"],
            "order_type": document["order_type"],
            "type_label": ORDER_TYPE_LABELS.get(document["order_type"], ""),
            "order_no": document["order_no"],
            "customer_id": document["customer_id"],
            "customer_name": document["customer_name"],
            "business_date": document["business_date"],
            "date_label": short_date(document["business_date"]),
            "amount_cents": document["amount_cents"],
            "amount_text": money_short(document["amount_cents"]),
            "time_label": document.get("time_label", ""),
        }
        for document in result["documents"]
    ]
    return _envelope(
        documents=documents,
        truncated=result["truncated"],
        order_type=order_type,
        customer_id=customer_id,
        customer_name=(customer_brief or {}).get("name", ""),
        date_range=date_range,
    )


@mobile_api_bp.get("/orders/<order_type>/<int:document_id>")
def order_detail(order_type: str, document_id: int):
    if order_type not in ORDER_TYPE_LABELS:
        return _error("单据类型不正确。", 400)
    document = readonly_queries.document_detail(order_type, document_id)
    if document is None:
        return _error("这张单据不存在，可能已作废或删除。", 404)
    items = [
        {
            "name": item["name"],
            "unit": item["unit"],
            "quantity": item["quantity"],
            "quantity_text": quantity_text(
                item["quantity"], three_dp=item["quantity_3dp"], unit=item["unit"]
            ),
            "unit_price_cents": item["unit_price_cents"],
            "unit_price_text": money_exact(item["unit_price_cents"]),
            "subtotal_cents": item["subtotal_cents"],
            "subtotal_text": money_exact(item["subtotal_cents"]),
        }
        for item in document["items"]
    ]
    status_labels = {"draft": "草稿", "saved": "正式保存", "printed": "已打印", "void": "已作废"}
    return _envelope(
        document={
            "id": document["id"],
            "order_no": document["order_no"],
            "order_type": document["order_type"],
            "type_label": ORDER_TYPE_LABELS[document["order_type"]],
            "customer_name": document["customer_name"],
            "business_date": document["business_date"],
            "date_label": month_day_label(document["business_date"]),
            "status": document["status"],
            "status_label": status_labels.get(document["status"], document["status"]),
            "total_cents": document["total_cents"],
            "total_text": money_exact(document["total_cents"]),
            "notes": document["notes"],
            "items": items,
        }
    )


# ---------------------------------------------------------------------------
# 商品价
# ---------------------------------------------------------------------------

@mobile_api_bp.get("/products")
def products():
    q = (request.args.get("q") or "").strip()
    # 排序两档，与客户页同构（2026-10-22 用户要求「和客户页是一样的」）：
    # 拼音（默认）/ 价格（最低价）。商品没有欠款概念，所以第二档用价格。
    sort = (request.args.get("sort") or "").strip()
    if sort not in {"pinyin", "price"}:
        sort = "pinyin"
    order = (request.args.get("order") or "").strip()
    if order not in {"asc", "desc"}:
        order = ""
    result = readonly_queries.product_price_summary(q, sort=sort, order=order)
    items = [
        {
            "id": product["id"],
            "name": product["name"],
            "unit": product["unit"],
            "price_count": product["price_count"],
            "min_price_cents": product["min_price_cents"],
            "max_price_cents": product["max_price_cents"],
            "min_price_text": (
                None if product["min_price_cents"] is None else money_short(product["min_price_cents"])
            ),
            "max_price_text": (
                None if product["max_price_cents"] is None else money_short(product["max_price_cents"])
            ),
        }
        for product in result["products"]
    ]
    return _envelope(
        products=items,
        truncated=result["truncated"],
        query=q,
        sort=sort,
        order=order,
    )


@mobile_api_bp.get("/products/<int:product_id>")
def product_detail(product_id: int):
    """商品详情：这个商品在每个客户处的专属价。

    2026-10-24（用户要求，第七张截图）：App 详情页顶部固定商品名，并在下面加了
    「搜索框 + 拼音/价格 + 正倒序箭头」。三个参数都交给服务端算 ——
    拼音匹配只有一套实现（`utils/pinyin.py`），客户端自己过滤就会分叉。
    """
    q = (request.args.get("q") or "").strip()
    sort = (request.args.get("sort") or "").strip()
    if sort not in {"price", "pinyin"}:
        sort = "price"
    order = (request.args.get("order") or "").strip()
    if order not in {"asc", "desc"}:
        order = "desc"
    detail = readonly_queries.product_customer_prices(product_id, q=q, sort=sort, order=order)
    if detail is None:
        return _error("这个商品不存在，可能已被删除。", 404)
    prices = []
    for price in detail["prices"]:
        special = price["price_cents"]
        recent = price["last_price_cents"]
        differs = recent is not None and special != recent
        prices.append(
            {
                "customer_id": price["customer_id"],
                "customer_name": price["customer_name"],
                "customer_deleted": price["customer_deleted"],
                "price_cents": special,
                "price_text": money_short(special),
                "last_price_cents": recent,
                "last_price_text": None if recent is None else money_short(recent),
                "updated_label": price["updated_label"],
                "differs": differs,
                "gap_text": money_short(abs(int(special) - int(recent))) if differs else "",
            }
        )
    return _envelope(
        product=detail["product"],
        prices=prices,
        price_count=detail["price_count"],
        truncated=detail["truncated"],
        query=q,
        sort=sort,
        order=order,
        customers_without_price=detail["customers_without_price"],
    )


# ---------------------------------------------------------------------------
# 元信息（客户端启动时确认地址对不对、服务活不活）
# ---------------------------------------------------------------------------

@mobile_api_bp.get("/meta")
def meta():
    device = mobile_pairing.device_from_cookie(request.cookies.get(mobile_pairing.COOKIE_NAME))
    return _envelope(
        shop_name=current_app.config.get("ERP_SHOP_NAME", ""),
        app_version=current_app.config.get("ERP_VERSION", ""),
        host=request.host,
        server_time=timestamp_label(datetime.now().isoformat(timespec="seconds")),
        order_types=[{"type": key, "label": ORDER_TYPE_LABELS[key]} for key in ORDER_TYPE_ORDER],
        paired=device is not None,
        device={
            "label": (device or {}).get("label", ""),
            "paired_at": timestamp_label((device or {}).get("paired_at", "")),
            "last_seen_at": timestamp_label((device or {}).get("last_seen_at", "")),
        },
    )
