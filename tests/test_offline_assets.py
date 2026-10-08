"""E14：Bootstrap 本地化（离线资产）。

背景：`base.html` 曾用 jsdelivr CDN 引 Bootstrap 5.3.3 CSS，断网时布局退化。
本轮改为 `static/vendor/bootstrap/bootstrap.min.css` 本地引用，并锁：
1）模板引的是本地 static 路径，不含任何 CDN 域名；
2）该静态文件可被 Flask 正常 200 返回；
3）全模板/静态源不再有残留的 bootstrap CDN 外链。
"""
from pathlib import Path

from erp import create_app
from erp.db import init_db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PROJECT_ROOT / "app" / "erp" / "templates"
VENDOR_CSS = PROJECT_ROOT / "app" / "erp" / "static" / "vendor" / "bootstrap" / "bootstrap.min.css"

CDN_MARKERS = ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "stackpath.bootstrapcdn.com")


def _client():
    init_db()
    return create_app().test_client()


def test_bootstrap_vendor_file_exists_and_is_real_css():
    assert VENDOR_CSS.exists(), "本地 bootstrap.min.css 不存在"
    text = VENDOR_CSS.read_text(encoding="utf-8", errors="ignore")
    assert "Bootstrap  v5.3.3" in text
    # 是真实样式表，不是错误页/占位符。
    assert len(text) > 100_000


def test_base_template_links_local_bootstrap():
    html = _client().get("/orders/new").get_data(as_text=True)
    assert "/static/vendor/bootstrap/bootstrap.min.css" in html
    for marker in CDN_MARKERS:
        assert marker not in html, f"页面仍外链 CDN：{marker}"


def test_local_bootstrap_served_with_200():
    client = _client()
    resp = client.get("/static/vendor/bootstrap/bootstrap.min.css")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Bootstrap  v5.3.3" in body


def test_no_cdn_bootstrap_reference_left_in_templates():
    offenders = []
    for path in TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in CDN_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)} -> {marker}")
    assert not offenders, "仍有模板外链 CDN：" + "; ".join(offenders)
