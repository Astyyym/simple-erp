# 桌面壳（pywebview + WebView2）UI 可自验

## 适用场景

需要确认**桌面壳里某个控件到底画没画出来**（联想浮层、下拉、弹层、主题色），而手上只有「DOM 说 open=true」这种间接证据时。

**结论先行：这不是「不可自验」项。** 本机 `.venv-win` 里有 pywebview，能起真实 WebView2 窗口、跑 JS 探针，并**只截应用自身窗口**的屏。两条证据（DOM 状态 + 真实像素）都能自己拿到。

## 为什么必须两路证据

| 证据 | 怎么读 | 证明什么 |
|---|---|---|
| DOM 状态 | `matches(':popover-open')`、computed `display/visibility/opacity`、`getBoundingClientRect()`、`elementFromPoint()` | 元素存在、已布局、命中最上层 |
| 渲染像素 | 截**应用自身窗口**的屏 | 宿主是否真的绘制了 |

DOM 全对而用户什么都看不见**是会发生的**（原生 `<datalist>` 就是活例）。绝不能拿「代码读起来对」的那一路去关掉这个缺口。

## 怎么跑

1. 起隔离服务（**不要碰正式数据根**）：

```bash
ERP_DATA_ROOT="<scratch>/root" ERP_CONFIG_PATH="<scratch>/root/config.json" \
ERP_LOCATION_FILE="<scratch>/location.json" ERP_PORT=5030 ERP_DISABLE_AUTH=1 \
.venv-win/Scripts/python.exe app/app.py   # 用 background=true 起
```

2. 用 Python 直连 urllib POST 造数据（**curl 会 400**，见下方坑）。
3. 写一个 pywebview 探针脚本（照抄全局技能 `windows-desktop-ui-debugging/scripts/probe-pywebview-shell.py` 的结构）：`webview.create_window(...)` 加载页面 → `webview.start(worker)` → 在 worker 里 `win.evaluate_js(...)` 读 DOM，再用 PowerShell + `System.Drawing` 的 `GetWindowRect` + `CopyFromScreen` **只截该窗口矩形**。
4. 用 `vision_analyze` 读截图，确认像素层面真的画出来了。

## 实测结论（2026-10-13 本机，WebView2 = Edg/154）

同一个宿主里对照：

| 对照 | DOM | 像素 |
|---|---|---|
| 原生 `<datalist>`（最小对照页） | `input.list` 存在、`options.length=3`、输入框聚焦值为 `a` | **候选弹层一个像素都没画**（箭头画了） |
| 本项目的自有 popover 浮层 | `:popover-open=true`、`visibility:visible`、`elementFromPoint` 命中候选 | **真的画出来了**（截屏可见白底候选行） |

应用页实测（真实 WebView2 窗口）：

- 客户列表页搜索框输 `czk` → 浮层画出「陈紫坤消防器材店」；`↓`+`Enter` 带入值、浮层关闭。
- 销售开单页商品名框输 `mhq` → 浮层画出「灭火器4kg 个 · ¥60.00」；`↓`+`Enter` 带入 `product_id=2`、单位=个。
- 两页 `document.querySelectorAll('datalist').length === 0`。

截图（scratch，24h 清理）：`…\scratch\erp-p3desktop\shots\{A-native-datalist,B-customers-suggest,C-order-entry-product-suggest}.png`。

## 坑

- **`curl -d "name=中文"` 会返回 400 Bad Request**（Body 编码问题），服务其实没坏。改用 Python `urllib.request` + `urlencode` + `.encode()`，正常 200/302。排查服务端故障前先排除这一点。
- 探针里 `win.evaluate_js` 传字符串表达式；要返回对象就写成 `(()=>{...})()` 自执行形式。
- 抓屏必须截**应用自身窗口矩形**（`MainWindowHandle` + `GetWindowRect`），截全桌面会被遮挡窗口挡住目标控件导致误判。
- `vision_analyze` 偶尔会把「独立浮层」看成「第二个输入框」。判定浮层是否真的独立时，**用坐标裁剪放大再问一次**，或直接比对探针给出的 `getBoundingClientRect()`。
- WebView2 版本会变，datalist 的行为是「随宿主版本漂移」的事实，不是永久性质；换机器/升级后重跑对照实验。

## 报告口径

分开说清：拿了哪路证据、哪路没拿到、还剩什么是 `unverified`。「DOM 说 open、截屏也画出来了」是报告；「它能用」不是。
