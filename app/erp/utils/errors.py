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


def error_response(message: str, status: int = 400, *, title: str | None = None, back_url: str | None = None,
                   severity: str = "error"):
    """把业务校验失败的原因返回给调用方，按调用方类型选择 JSON 或页面。

    severity='error' 表示操作没做成；severity='warning' 表示操作做了一部分、
    有内容被保留（例如清空回收站时部分记录被合法引用挡住），页面用中性样式而不是红色报错。
    """
    text = str(message)
    if wants_json():
        return jsonify({"error": text, "severity": severity}), status
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
