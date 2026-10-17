# -*- coding: utf-8 -*-
"""临时局域网下载页：手机扫码下载 APK，下载完成后自动关闭。

用法（在项目根目录）：
    .venv-win\\Scripts\\python.exe packaging\\serve_apk.py dist\\simple-erp-mobile-v1.0.0.apk [端口]

只做两件事：给一个手机友好的下载页、把 APK 发出去。不列目录、不暴露其它文件。
"""
import http.server
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def lan_ip() -> str:
    try:
        from erp.mobile_server import lan_ip as _lan_ip

        return _lan_ip()
    except Exception:
        return "127.0.0.1"


def human(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python packaging/serve_apk.py <apk 路径> [端口]")
        return 2
    apk = Path(sys.argv[1])
    if not apk.is_absolute():
        apk = ROOT / apk
    if not apk.exists():
        print(f"找不到 APK: {apk}")
        return 2
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    size = apk.stat().st_size
    filename = apk.name
    url = f"http://{lan_ip()}:{port}/"

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>简单ERP 手机端</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 28px 20px 40px; font: 16px/1.7 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         background: #f7f8fa; color: #111827; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #16181d; color: #e5e7eb; }}
    .card {{ background: #1f2229 !important; border-color: #2c3038 !important; }}
    .meta {{ color: #9ca3af !important; }} .note {{ color: #9ca3af !important; }} }}
  .card {{ max-width: 460px; margin: 0 auto; background: #fff; border: 1px solid #e5e7eb;
          border-radius: 14px; padding: 22px 20px; }}
  h1 {{ font-size: 20px; margin: 0 0 6px; }}
  .meta {{ color: #6b7280; font-size: 13px; margin-bottom: 20px; }}
  a.btn {{ display: block; text-align: center; padding: 15px 16px; border-radius: 10px;
          background: #2563eb; color: #fff; text-decoration: none; font-weight: 600; font-size: 17px; }}
  a.btn:active {{ background: #1d4ed8; }}
  ol {{ margin: 20px 0 0; padding-left: 20px; font-size: 14px; }}
  li {{ margin-bottom: 8px; }}
  .note {{ margin-top: 18px; font-size: 12.5px; color: #6b7280; }}
</style>
</head>
<body>
  <div class="card">
    <h1>简单ERP 手机端</h1>
    <div class="meta">{filename} &nbsp;·&nbsp; {human(size)}</div>
    <a class="btn" href="/download">下载安装包</a>
    <ol>
      <li>下载完成后点开安装；提示「未知来源」时允许这一次。</li>
      <li>打开 App，填电脑地址（设置 → 手机访问 里显示的那个）。</li>
      <li>电脑上点「生成配对码」，把 6 位数字填进手机。</li>
    </ol>
    <div class="note">这个下载页在文件下载完成后会自动关闭，装完就可以不管它。</div>
  </div>
</body>
</html>
"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # 不刷日志
            pass

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/download":
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(size))
                self.end_headers()
                with open(apk, "rb") as f:
                    while True:
                        chunk = f.read(64 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                threading.Timer(10.0, httpd.shutdown).start()
            else:
                self.send_error(404)

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"下载页: {url}")
    print(f"文件: {filename}  {human(size)}")
    print("下载完成后 10 秒自动关闭", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print("已关闭", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
