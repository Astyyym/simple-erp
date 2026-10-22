"""手机端的**独立 Flask app**（第二监听 5001 用）。

为什么不复用 `create_app()`（决策文档 §4 约束 8，理由都是硬的）：

1. `create_app()` 注册了全部 9 个桌面蓝图并执行 `init_db()`（含迁移）。
   复用它，`/customers/` 就是 200 —— 「蓝图外一律 404」直接失效；更糟的是
   第二监听会**再跑一次迁移**（迁移只应由主应用跑一次）。
2. 独立 app 只注册 `mobile_bp`，蓝图外天然 404，不靠 `if` 判断。
3. 它不注册主应用的 `reject_cross_origin_mutations`（`__init__.py:142`）——
   配对 POST 因此不会被那条 hook 误判成「跨站写请求」。

数据根不必另传：`db_path()` 走 `project_path()`（`config.py:159`），
同一进程内天然与主应用一致。**禁止**为第二监听新起进程或另指定 `ERP_DATA_ROOT`。
"""
from __future__ import annotations

from flask import Flask, request

from erp.config import load_config
from erp.routes.mobile import mobile_bp
from erp.routes.mobile_api import mobile_api_bp
from erp.utils.filter_chips import ORDER_TYPE_LABELS
from erp.utils.mobile_format import (
    amount_band,
    band,
    money_exact,
    money_short,
    month_day_label,
    quantity_text,
    short_date,
    timestamp_label,
    weekday_label,
)

try:  # 版本号：源码模式读 VERSION；读不到不影响手机端可用
    from erp import app_version
except ImportError:  # pragma: no cover
    def app_version() -> str:  # type: ignore[misc]
        return ""


def create_mobile_app() -> Flask:
    """构造第二监听的 app：只挂 `mobile_bp` + static，**不调 `init_db()`**。"""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "local-fire-erp-mobile-readonly"
    app.config["ERP_VERSION"] = app_version()
    try:
        config = load_config()
    except Exception:  # noqa: BLE001 — 配置文件缺失时手机端仍应能起来并提示
        config = {}
    app.config["ERP_SHOP_NAME"] = str(config.get("shop_name") or "简单ERP")

    app.jinja_env.globals.update(
        money_short=money_short,
        money_exact=money_exact,
        band=band,
        amount_band=amount_band,
        quantity_text=quantity_text,
        short_date=short_date,
        month_day_label=month_day_label,
        weekday_label=weekday_label,
        timestamp_label=timestamp_label,
        order_type_labels=ORDER_TYPE_LABELS,
        shop_name=app.config["ERP_SHOP_NAME"],
        app_version=app.config["ERP_VERSION"],
    )

    # 静态资源：长缓存 + 内容指纹地址（与主 app 的 static_url 同一实现）。
    # 第二监听有自己的 Flask 实例，主 app 的 SEND_FILE_MAX_AGE_DEFAULT / 模板全局
    # 都不会自动带过来 —— 之前切页慢就是因为这里发的是默认 no-cache，
    # 每次导航都要重新验证 mobile.css(29KB) + mobile.js(22KB)。
    from erp import _static_url

    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000
    app.jinja_env.globals["static_url"] = _static_url

    @app.after_request
    def _mobile_headers(response):
        if request.path.startswith("/static/"):
            # 地址里带内容指纹，内容变则 URL 变，可以放心长缓存。
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # 页面是实时数据，不能缓存内容。
            # 注意：**不要**在这里设 Connection 头 —— WSGI 规范（PEP 3333）把
            # Connection 定义为 hop-by-hop 头，waitress 会直接抛 AssertionError
            # （实测启动后每个请求 500）。连接复用是 waitress 自己的职责，
            # 它默认就按 HTTP/1.1 保持长连接，不需要应用插手。
            response.headers["Cache-Control"] = "no-store"
        return response

    app.register_blueprint(mobile_bp)
    # JSON 接口：给原生安卓客户端用。与 HTML 蓝图共用同一套只读查询，
    # 口径不分叉；蓝图外仍然天然 404（第二监听只挂这两个蓝图）。
    app.register_blueprint(mobile_api_bp)
    return app
