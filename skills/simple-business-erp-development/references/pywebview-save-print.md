# PyWebView/WebView2 save-and-print workflow

## Symptom

A Flask form submitted from the desktop WebView with `target="_blank"` opens the system browser, which displays `405 Method Not Allowed`.

## Root cause

PyWebView's EdgeChromium `NewWindowRequested` handler marks the popup handled and opens only the requested URI externally. A POST form's method/body/redirect context is not reliably transferred across the WebView → system-browser boundary. Changing Flask `302` to `303` does not fix this because the break can occur before the redirect is followed.

## Evidence pattern

Confirm all layers before editing:

1. Identify the actual running EXE and owning process for port 5000.
2. Verify `/health` reports the expected packaged version.
3. POST directly to the running EXE and inspect status/Location.
4. Confirm the packed template still contains or no longer contains `target="_blank"` logic.
5. Distinguish the two likely 405s: `GET /orders/create` versus `POST /orders/<id>/pdf`.

## Durable implementation

- Never open a new browser window by submitting a POST form with `_blank` inside PyWebView.
- Intercept save/print with JavaScript and `preventDefault()`; preserve the current WebView navigation context rather than relying on authentication state.
- POST with `fetch` and `X-Requested-With: fetch`.
- Return `200 JSON` containing `order_id`, `order_no`, absolute `pdf_url`, `detail_url`, and the correct sale/return `next_url`.
- Open the PDF separately with a pure GET URL.
- Show a persistent success panel with manual actions: open print page, view order, continue order entry.
- Do not immediately clear or redirect the entry page.
- Disable the submit button while saving; distinguish “save failed” from “saved but popup blocked.”
- Keep ordinary non-JavaScript submission as a fallback, using `303` to a GET page.

## Verification

- Add Flask tests for both sale and return JSON responses.
- Assert templates no longer dynamically assign POST forms to `_blank`.
- Run the full suite against a clean temporary database.
- Rebuild the Windows EXE and ZIP.
- Start the real EXE and verify `/health` reports the new version.
- Against the EXE, POST with the fetch header for both sale and return, then GET each returned PDF URL and check for `%PDF`.
- For the final acceptance, manually click save/print inside the real desktop WebView; HTTP probes alone do not exercise `NewWindowRequested`.

## Scrollbar-free in-page PDF display

- Use an application-controlled rendering layer for the real PDF pages when the screen preview needs hand panning; hiding the native reader's outer scrollbar does not control its internal mouse/scroll behavior. Keep the original PDF bytes separately for download, native save and print; never print the ERP page or rasterize the canvas back into the authoritative PDF.
- Vendor the display library and matching worker/resources with their licenses locally, and include the entire Flask static directory in the PyInstaller spec. Verify `.mjs` responses have a JavaScript MIME type; a source preview passing does not prove a frozen build contains its assets.
- Clip the preview viewport, capture the pointer on press, release on up/cancel/lost capture/close, and clamp each translation axis so all paper edges remain reachable. Center an axis when the paper fits; reset to the top on an overflowing vertical axis. Confirm pan/zoom using real mouse events, not only DOM string assertions.
- Render all original pages. Use a clearly labelled real generated multi-page PDF fixture to test multi-page panning if the business generator currently produces one page; do not describe the fixture as a saved multi-page order.
- Report native print dispatch, the native dialog, and actual printer output separately. A hidden original-PDF frame or a mocked `print()` invocation proves only its code path, not the desktop print dialog or physical output.
- Treat a `%PDF` header check as insufficient for validity. Evict cached bytes/object URLs and reset native-print readiness when parsing/rendering fails, then verify explicit retry fetches and renders the recovered server PDF without saving another order. Use a deliberately truncated real PDF fixture with the real renderer, not only a mock throwing an error.
- Reveal the existing panel before an in-flight-load guard, so close/reopen does not drop the reopen click or start duplicate render tasks. Test both reopening and remaining closed until rendering completes; completion text must follow the actual panel state.

## 一次点击只渲染一次（2026-10-11）

桌面统一预览页最容易犯的错：**一次「保存并打印」把 PDF 渲染两遍**（预览页先渲染一遍，iframe 再请求同一路径又渲染一遍）。实测 10 行单：预览 1.31s + iframe 0.57s ≈ 1.9s，其中含首次导入 WeasyPrint 0.75s；用户感知为「保存并打印卡顿」，而 POST 保存本身只要 0.22~0.38s。

落地约束：

- `?desktop_preview=1` 时路由**不生成 PDF**，只渲染 `desktop_pdf_preview.html` 工具栏；PDF 由 iframe 用同一路径（不带 `desktop_preview`）请求一次。实现入口：`erp/utils/pdf.py` 的 `render_desktop_preview` / `wants_desktop_preview`。
- **不渲染 ≠ 不校验**：预览模式必须先跑一遍与渲染同口径的校验函数（`ensure_order_printable` / `ensure_purchase_printable` / `ensure_account_summary_printable`），否则订单不存在、状态非法会从 404/400 退化成「空白工具栏」。
- 校验函数与渲染函数必须共用同一处实现（抽成 `_load_*_context` 或 `ensure_*`），否则两套口径会漂移。
- 启动后台预热 WeasyPrint 导入（`prewarm_pdf_engine`，只导入不渲染），把首次 0.75s 挪到开机；预热失败不得影响启动。
- 实测（隔离库、10 行单、已预热）：渲染调用 **2 → 1**，预览页 0.012s + iframe 0.81s = **0.822s**（原 1.9s）。
- 回归：`tests/test_pdf_render_once.py` 用计数探针钉住「预览页 0 次、完整链路 1 次」，并断言错误契约与浏览器直连 PDF 行为不变。

## Packaging pitfall

Before rebuilding or recompressing `dist/消防ERP`, terminate every running packaged ERP process. Otherwise PyInstaller or `Compress-Archive` can fail on locked files such as `logs/app.log` or bundled DLLs. Preserve and restore writable `data/`, `logs/`, `temp_pdf/`, `backups/`, and `imports/` around smoke tests.