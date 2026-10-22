"""手机只读端蓝图 `/m/*`：浏览器直连，只 GET。

=========================== 不可动约束（写代码前先读） ===========================
1. **不调 `customer_balance_cents()`**：它在 `app/` 内零调用（只有测试在调），
   余额只用 `get_customer_account_ledger()`。
2. **`mobile_bp` 外一律 404，不是 403**：403 会告诉扫描者「这里有个东西」。
   天然 404 由「第二监听是独立 app、只挂本蓝图」保证（见 `mobile_app.py`）。
3. **只允许 GET；唯一例外是配对提交本身 `POST /m/pair`。**
   它只写 `data/mobile_devices.json`，绝不写 `erp.db`；也不经过主应用的
   `reject_cross_origin_mutations`（那正是第二监听必须独立成 app 的原因）。
   设置页同样**没有写端**：主题只是本地 `data-theme`，「重新配对」只是跳转；
   彻底断开只能在桌面端撤销设备。
4. 第二监听端口固定 `5001`，不自动漂移（端口是配对 base_url 的一部分）。
5. 主应用 `5000` 永绑 `127.0.0.1`。
6. 阶段一移动端**不落本地数据**：撤销设备即彻底失联。将来有人加缓存就悄悄破掉这条。
7. 「只读」由测试保证（`tests/test_mobile_readonly.py`），不能只靠这里的 `if`。
9. 所有查询走 `services/readonly_queries.py` 的 `mode=ro` 连接。
================================================================================
"""
from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    g,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from erp.services import mobile_pairing, readonly_queries
from erp.utils.filter_chips import ORDER_TYPE_LABELS
from erp.utils.mobile_format import (
    month_day_label,
    quantity_text,
    short_date,
    timestamp_label,
    weekday_label,
)

mobile_bp = Blueprint("mobile", __name__, url_prefix="/m")

PAIR_FORM_FIELD = "code"


def _plain(body: str, status: int, *, title: str = "") -> "object":
    """最小响应：只用 media type，不渲染任何模板（拒非 GET 时用它）。"""
    response = make_response(body, status)
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    if title:
        response.headers["X-Mobile-Error"] = title
    return response


def _safe_next(value: str | None) -> str:
    """配对后跳转只接受站内相对路径（防开放重定向）。"""
    candidate = (value or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return url_for("mobile.today")
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return url_for("mobile.today")
    return candidate


@mobile_bp.before_request
def guard_readonly_and_pairing():
    """设备门 + 拒非 GET。`POST /m/pair` 是唯一例外。"""
    # 配对页自己的 GET / POST 都不过设备门：未配对时唯一可达的页面。
    if request.endpoint in {"mobile.pair", "mobile.pair_submit"}:
        return None
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        return _plain("手机端是只读页面，不支持这个操作。", 405)
    device = mobile_pairing.device_from_cookie(request.cookies.get(mobile_pairing.COOKIE_NAME))
    if device is None:
        return redirect(url_for("mobile.pair", next=request.path))
    g.mobile_device = device
    mobile_pairing.remember_success(device, request.remote_addr or "")
    return None


# ---------------------------------------------------------------------------
# 今日概览（新首屏）
# ---------------------------------------------------------------------------

@mobile_bp.get("/")
def today():
    overview = readonly_queries.today_overview()
    today_iso = overview["date"]
    for entry in overview["types"]:
        entry["label"] = ORDER_TYPE_LABELS[entry["type"]]
    return render_template(
        "mobile/today.html",
        nav_key="today",
        overview=overview,
        today_iso=today_iso,
        date_label=month_day_label(today_iso),
        weekday=weekday_label(today_iso),
    )


# ---------------------------------------------------------------------------
# 客户
# ---------------------------------------------------------------------------

@mobile_bp.get("/customers")
def customers():
    q = (request.args.get("q") or "").strip()
    only_debt = (request.args.get("filter") or "") == "debt"
    # 排序：默认拼音（用户 2026-10-18 定稿），另一个选项是按欠款。
    # 只认这两个值，别的任何输入都退回默认，避免被拼错的 query 弄出空列表。
    sort = (request.args.get("sort") or "").strip()
    if sort not in {"pinyin", "debt"}:
        sort = "pinyin"
    result = readonly_queries.list_customers(q, only_debt=only_debt, sort=sort)
    return render_template(
        "mobile/customers/list.html",
        nav_key="customers",
        customers=result["customers"],
        truncated=result["truncated"],
        query=q,
        only_debt=only_debt,
        sort=sort,
    )


@mobile_bp.get("/customers/<int:customer_id>")
def customer_detail(customer_id: int):
    detail = readonly_queries.customer_detail(customer_id)
    if detail is None:
        return _not_found_page("这个客户不存在，可能已被删除。", back_url=url_for("mobile.customers"))
    price_list = readonly_queries.customer_price_list(customer_id) or {
        "customer": None,
        "prices": [],
        "price_count": 0,
        "truncated": False,
    }
    ledger_labels = {**ORDER_TYPE_LABELS, "payment": "收款", "adjustment": "账务调整"}
    for entry in detail["entries"]:
        entry["date_label"] = month_day_label(entry.get("date", ""))
        entry["type_label"] = ledger_labels.get(entry.get("type"), entry.get("type"))
    for document in detail["documents"]:
        document["type_label"] = ORDER_TYPE_LABELS.get(document["order_type"], document["order_type"])
    return render_template(
        "mobile/customers/detail.html",
        nav_key="customers",
        detail=detail,
        price_list=price_list,
    )


@mobile_bp.get("/customers/<int:customer_id>/prices")
def customer_prices(customer_id: int):
    """客户的全部专属价：商品名 + 该客户价格，点商品看这个商品在所有客户处的价。"""
    price_list = readonly_queries.customer_price_list(customer_id)
    if price_list is None:
        return _not_found_page("这个客户不存在，可能已被删除。", back_url=url_for("mobile.customers"))
    return render_template(
        "mobile/customers/prices.html",
        nav_key="customers",
        price_list=price_list,
    )


# ---------------------------------------------------------------------------
# 单据
# ---------------------------------------------------------------------------

@mobile_bp.get("/orders")
def orders():
    order_type = (request.args.get("order_type") or "").strip()
    if order_type not in ORDER_TYPE_LABELS:
        order_type = ""
    customer_id = request.args.get("customer_id", type=int) or 0
    customer_brief = readonly_queries.customer_brief(customer_id) if customer_id else None
    if customer_brief is None:
        customer_id = 0
    date_range = readonly_queries.resolve_mobile_range(request.args)
    today = date.today()
    years = sorted({today.year - 2, today.year - 1, today.year, int(date_range["year"])})
    result = readonly_queries.list_documents(
        order_type=order_type,
        start_date=date_range["start_date"],
        end_date=date_range["end_date"],
        customer_id=customer_id or None,
    )
    for document in result["documents"]:
        document["type_label"] = ORDER_TYPE_LABELS.get(document["order_type"], document["order_type"])
        document["day_label"] = short_date(document["business_date"])
    return render_template(
        "mobile/orders/list.html",
        nav_key="orders",
        documents=result["documents"],
        truncated=result["truncated"],
        order_type=order_type,
        customer_id=customer_id,
        customer_brief=customer_brief,
        date_range=date_range,
        years=years,
        today_iso=today.isoformat(),
    )


@mobile_bp.get("/orders/<order_type>/<int:document_id>")
def order_detail(order_type: str, document_id: int):
    if order_type not in ORDER_TYPE_LABELS:
        return _not_found_page("单据类型不正确。", back_url=url_for("mobile.orders"))
    document = readonly_queries.document_detail(order_type, document_id)
    if document is None:
        return _not_found_page("这张单据不存在，可能已作废或删除。", back_url=url_for("mobile.orders"))
    document["type_label"] = ORDER_TYPE_LABELS[document["order_type"]]
    document["status_label"] = {
        "draft": "草稿",
        "saved": "正式保存",
        "printed": "已打印",
        "void": "已作废",
    }.get(document["status"], document["status"])
    for item in document["items"]:
        item["quantity_label"] = quantity_text(
            item["quantity"], three_dp=item["quantity_3dp"], unit=item["unit"]
        )
    return render_template(
        "mobile/orders/detail.html",
        nav_key="orders",
        document=document,
    )


# ---------------------------------------------------------------------------
# 商品价
# ---------------------------------------------------------------------------

@mobile_bp.get("/products")
def products():
    q = (request.args.get("q") or "").strip()
    result = readonly_queries.product_price_summary(q)
    return render_template(
        "mobile/products/list.html",
        nav_key="products",
        result=result,
        query=q,
    )


@mobile_bp.get("/products/<int:product_id>")
def product_detail(product_id: int):
    """商品详情：这个商品在**每个客户**那里的专属价（用户点名的那个洞）。"""
    detail = readonly_queries.product_customer_prices(product_id)
    if detail is None:
        return _not_found_page(
            "这个商品不存在，可能已被删除。", back_url=url_for("mobile.products")
        )
    customer_id = request.args.get("customer_id", type=int) or 0
    if customer_id and readonly_queries.customer_brief(customer_id) is None:
        customer_id = 0
    for price in detail["prices"]:
        special = price["price_cents"]
        recent = price["last_price_cents"]
        price["differs"] = recent is not None and special != recent
        price["gap_cents"] = abs(int(special) - int(recent)) if price["differs"] else 0
    back_url = (
        url_for("mobile.customer_prices", customer_id=customer_id)
        if customer_id
        else url_for("mobile.products")
    )
    return render_template(
        "mobile/products/detail.html",
        nav_key="products",
        detail=detail,
        customer_id=customer_id,
        back_url=back_url,
    )


# ---------------------------------------------------------------------------
# 设置（无写端）
# ---------------------------------------------------------------------------

@mobile_bp.get("/settings")
def settings():
    device = g.get("mobile_device") or {}
    address = request.host
    return render_template(
        "mobile/settings.html",
        nav_key="settings",
        address=address,
        device=device,
        device_paired_label=timestamp_label(device.get("paired_at", "")),
        last_success_label=timestamp_label(mobile_pairing.last_success_at()),
        app_version=current_app.config.get("ERP_VERSION", ""),
        shop_name=current_app.config.get("ERP_SHOP_NAME", ""),
    )


# ---------------------------------------------------------------------------
# 配对（唯一 POST）
# ---------------------------------------------------------------------------

@mobile_bp.get("/pair")
def pair():
    return render_template(
        "mobile/pair.html",
        nav_key="pair",
        error="",
        next_path=_safe_next(request.args.get("next")),
        lock_seconds=mobile_pairing.lock_remaining_seconds(),
    )


@mobile_bp.post("/pair")
def pair_submit():
    next_path = _safe_next(request.form.get("next"))
    code = request.form.get(PAIR_FORM_FIELD, "")
    label = (request.form.get("label") or "").strip()
    cookie_value, error = mobile_pairing.verify_pair_code(
        code,
        label=label or _device_label_from_agent(request.headers.get("User-Agent", "")),
        ip=request.remote_addr or "",
        # 这个蓝图只服务浏览器页面，所以固定报 browser。
        platform=mobile_pairing.PLATFORM_BROWSER,
    )
    if error:
        return (
            render_template(
                "mobile/pair.html",
                nav_key="pair",
                error=error,
                next_path=next_path,
                lock_seconds=mobile_pairing.lock_remaining_seconds(),
            ),
            400,
        )
    response = redirect(next_path)
    response.set_cookie(
        mobile_pairing.COOKIE_NAME,
        cookie_value,
        max_age=400 * 24 * 3600,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


def _device_label_from_agent(user_agent: str) -> str:
    """从 UA 里挑一个能认出手机的短名字（只作设备列表展示用）。"""
    agent = (user_agent or "").lower()
    for token, name in (
        ("iphone", "iPhone"),
        ("ipad", "iPad"),
        ("android", "Android 手机"),
        ("windows", "Windows 浏览器"),
        ("macintosh", "Mac 浏览器"),
    ):
        if token in agent:
            return name
    return "手机"


def _not_found_page(message: str, *, back_url: str):
    return (
        render_template("mobile/not_found.html", nav_key="", message=message, back_url=back_url),
        404,
    )


@mobile_bp.app_errorhandler(404)
def handle_404(error):  # pragma: no cover - 只影响手机端体验，不影响只读契约
    return (
        render_template(
            "mobile/not_found.html",
            nav_key="",
            message="你访问的页面不存在，可能链接已失效。",
            back_url=url_for("mobile.today"),
        ),
        404,
    )


@mobile_bp.app_errorhandler(500)
def handle_500(error):  # pragma: no cover
    return (
        render_template(
            "mobile/not_found.html",
            nav_key="",
            message="手机端读数据时出错了，请回到首页重试。",
            back_url=url_for("mobile.today"),
        ),
        500,
    )
