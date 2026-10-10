# 桌面壳 splash 先行（PyWebView）实现规则

用途：改 `desktop_app.py` 的启动流程，让「窗口先出现」以消掉双击后 2–4 秒黑屏观感时，**必须同时保住已有的启动契约**。写成「先开窗口」会挂死既有测试。

## 硬约束：数据库失败必须先抛错、且还没开窗口

`tests/test_desktop_startup.py::test_main_reports_real_database_failure_before_opening_webview` 要求：
真实数据库失败时，`desktop_app.main()` 要**立刻 raise**，而不是先把窗口开出来。

因此**不能**写成：

```python
# ✗ 会挂死该测试（exit 124 超时）：webview.start() 阻塞，永远走不到抛错
_window = webview.create_window(title, html=SPLASH_HTML, ...)
threading.Thread(target=_swap_to_app, daemon=True).start()
webview.start()
```

正确顺序（2026-10-09 已验证 **15 passed**）：

```python
def main():
    already_running = _port_is_open()
    startup_errors = queue.Queue()
    app = None
    if not already_running:
        # 1) 先同步建 app —— 失败立即 raise，不开窗口（保住契约）
        try:
            app = _create_app()          # 内部才 from erp import create_app
        except Exception as exc:
            raise RuntimeError(f"简单ERP启动失败：{exc}") from exc
        # 2) 再起服务线程（复用已建好的 app，不重复 create_app）
        threading.Thread(target=_run_server, args=(startup_errors,),
                         kwargs={"app": app}, daemon=True).start()

    # 3) 窗口用内联 splash 先行，webview.start() 启动 WebView2
    #    background_color 让「窗口出现→splash 绘制」之间不是刺眼纯白
    _window = webview.create_window(title, html=SPLASH_HTML,
                                    background_color=SPLASH_BACKGROUND, ...)

    # 4) 关键：必须等 splash **真正绘制**后才切主界面，并给最小停留时间
    splash_painted = threading.Event()
    painted_at = {}

    def _on_loaded():
        if not splash_painted.is_set():
            painted_at["t"] = time.perf_counter()
            splash_painted.set()

    def _swap_to_app():
        try:
            if not already_running:
                _wait_for_server(startup_errors=startup_errors)
            # 等 splash 首帧画出（8s 兜底防该事件缺失时永久停住）
            splash_painted.wait(timeout=8.0)
            started = painted_at.get("t")
            if started is not None:
                remaining = SPLASH_MIN_SECONDS - (time.perf_counter() - started)
                if remaining > 0:
                    time.sleep(remaining)
            _window.load_url(APP_URL)
        except Exception as exc:
            _window.load_html(_error_html(str(exc)))

    _window.events.loaded += _on_loaded
    threading.Thread(target=_swap_to_app, daemon=True).start()
    webview.start()
```

要点：

- `_create_app()` 把 `from erp import create_app` 放在函数内，模块级不再 import `erp`——这样导入 `desktop_app` 本身不付 app 的导入成本。
- `_run_server(startup_errors, *, app=None)` 支持传入预建 app，避免起服务时二次 `create_app`。
- `_run_server()` 仍保留 `startup_errors.put(exc)` 通道，另一个必须的测试 `test_database_startup_failure_is_logged_and_reported_immediately` 依赖它。

## ⚠️ 最容易踩的坑：splash「窗口出现」≠「splash 可见」

**2026-10-09 真实事故**：只做了「窗口先行」，用户双击后**根本没看到 splash**。

根因：服务在 Batch A 后 **~0.95s** 就绪，比窗口出现（~2.0s）还快。若 `_swap_to_app()` 只等「端口就绪」就 `load_url(APP_URL)`，那么 **~1.0s 就抢跑切主界面**，splash 的 HTML **根本没机会绘制**——用户从头到尾只看到一个**空白白窗**。

**必须同时做三件事**：

1. **切页前 `splash_painted.wait()`**：监听 `window.events.loaded` 拿到「splash 首帧已绘制」后才 `load_url`。
2. **最小停留 `SPLASH_MIN_SECONDS`（约 0.9s）**：否则 splash 只 flash 不足 0.1s，用户仍读成「没反应」。太少等于没有，太多拖慢启动。
3. **`background_color`**：窗口出现到 splash 绘制之间（WebView2 冷启动，可能 1s+）不能是刺眼纯白。

**取证方法**：抓 ERP 窗口矩形（不用全屏，全屏会被其它窗口挡住）：
1. `EnumWindows` 找到该 PID 的可见窗口 + 标题。
2. `SetForegroundWindow` + `GetWindowRect`，`ImageGrab.grab(bbox=rect)`。
3. 每 ~0.12s 存一帧，看 splash 到底哪一帧出现、停留多久、何时切成主界面。

## 为什么不是「纯粹先窗口」

纯「先窗口」能更早看到 splash，但会牺牲「数据库失败先抛错」这条既有契约（上面的测试会挂死）。本项目选择「先建 app（Batch A 后仅 ~0.45–0.55s）再开窗口」：WebView2 冷启动（~1.5s）仍与服务 bind + 首页加载并行，实测窗口可见中位 **~1.31s**，达标。**这是有意权衡，不是疏漏。**

## splash 品牌样式

- 内联 HTML，**不落外部文件**（避免 PyInstaller 多一个 data 条目）。
- 品牌图标复用侧栏 `.brand-mark` 的同一个 Keyline file-spreadsheet `<path>`（`base.html`）。
- 配色**直接取 `base.html` 的现有 token**：浅色 primary `#2563eb` / bg `#f3f5f8`；深色 primary `#3b82f6` / bg `#0f141d` 等。
- 主题跟随用 CSS `@media (prefers-color-scheme: dark)`，**splash 阶段不读 config**（读 config 会引入 IO 与 `erp.config` 依赖，拖慢首帧）。
- **已知边界**：`ui_theme` 显式设为 `dark` 而 OS 为浅色时，splash 浅、主界面深（`system` 档完全正确）。这是不读 config 的代价，须如实报告。

## 验收清单

1. `tests/test_desktop_startup.py` + `tests/test_desktop_pdf_save.py` **全绿**（尤其 `test_main_reports_real_database_failure_before_opening_webview` **不能挂死**）。
2. 冻结版窗口可见耗时（n≥5）明显早于基线。
3. 切主界面后确认加载的是**真实首页**（不是停在 splash）。
4. 失败路径：服务起不来时窗口显示错误提示，**不永久停在 splash**。
5. 「无闪烁 / 品牌观感 / 重复双击」属**真实桌面人工项**，脚本层不能替代。
