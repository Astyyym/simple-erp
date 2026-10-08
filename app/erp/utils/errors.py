"""统一的用户可见错误呈现。

背景（2026-10-07 用户反馈）：删除/批量操作失败时，后端把中文原因当纯文本返回
（`return str(exc), 400`），浏览器直接把这段文字当页面显示——用户看到的是一个
只有一行字的空白页，回收站/单据管理界面丢失，也不知道「为什么删不掉」。

这里把「原因」和「呈现」分开：
- 期望 JSON 的调用方（`X-Requested-With: fetch` 或 `Accept: application/json`）拿 JSON；
- 浏览器导航拿一个正常的页面（继承 base.html，带侧栏与「返回」按钮）。
"""
from __future__ import annotations

from flask import jsonify, render_template, request


def wants_json() -> bool:
    """前端 fetch 调用方要 JSON，浏览器导航要 HTML。"""
    if request.headers.get("X-Requested-With") == "fetch":
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept and "text/html" not in accept


def not_found(message: str, *, title: str = "页面不存在", back_url: str = "/"):
    """记录/页面不存在：统一走 404 中文错误呈现，而不是英文默认页或裸文本。"""
    return error_response(message, 404, title=title, back_url=back_url)


class RecordNotFound(ValueError):
    """记录（单据/快照/客户等）不存在：路由应呈现 404，而不是 400。

    继承 ValueError：既有的 `except ValueError`（把这类错误当业务校验失败处理）
    仍能兜住，行为不退化；只有需要区分状态码的路由才 `except RecordNotFound` 先转 404。

    分界：
    - ValueError（含本类的兄弟）→ 400（请求本身有问题：参数缺失、状态非法、筛选无数据）
    - RecordNotFound → 404（请求指向的资源不存在）
    """


def error_response(message: str, status: int = 400, *, title: str | None = None, back_url: str | None = None,
                   severity: str = "error"):
    """把业务校验失败的原因返回给调用方，按调用方类型选择 JSON 或页面。

    severity='error' 表示操作没做成；severity='warning' 表示操作做了一部分、
    有内容被保留（例如清空回收站时部分记录被合法引用挡住），页面用中性样式而不是红色报错。
    """
    text = str(message)
    if wants_json():
        # 两个键都要给：base.html 的行内删除读 `error`，开单页 fetch 读 `message`。
        # 少了任何一个都会把真实原因吞成通用文案。
        return jsonify({"error": text, "message": text, "severity": severity}), status
    return (
        render_template(
            "error.html",
            error_message=text,
            status_code=status,
            error_title=title or "操作未完成",
            back_url=back_url,
            severity=severity,
        ),
        status,
    )
