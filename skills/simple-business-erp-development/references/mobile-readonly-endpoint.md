# 手机只读端（浏览器直连，第二监听 5001）

2026-10-17 落地。需求与决策的权威文档是 `docs/需求/2026-10-17_安卓只读端架构决策.md`，
执行记录见 `开发短计划/2026-10-17_安卓只读端_执行计划.md`。本文只记**可复用的做法与坑**，
不重复产品口径。

## 结构：为什么是「独立的第二个 app」

```
主应用 5000（waitress, host 硬绑 127.0.0.1）
    └─ 9 个桌面蓝图 + init_db()（迁移只在这里跑）

第二监听 5001（同一进程，独立 Flask app）
    └─ 只挂 mobile_bp（/m/*）→ 蓝图外天然 404
         └─ services/readonly_queries.py（mode=ro 连接）
              └─ data/erp.db
```

- **不要复用 `create_app()`**：它会注册桌面蓝图（`/customers/` 就变 200）并跑 `init_db()`
  （第二监听会再迁移一次）。
- 独立 app 也因此**不注册** `reject_cross_origin_mutations`——配对 `POST /m/pair` 不会被误判
  成跨站写请求。这条反过来强制了「独立 app」这个结构。
- 桌面端 `desktop_app.py` / 源码 `app/app.py` 在开关打开时起监听、退出时 `stop_mobile_listener()`；
  开关状态存 `config.json` 的 `mobile_access_enabled`。

## 只读的三层保证（缺一层就只是口头承诺）

| 层 | 实现 | 证据 |
|---|---|---|
| 物理层 | `sqlite3.connect("file:<db>?mode=ro", uri=True)` + `PRAGMA busy_timeout=4000` + `PRAGMA query_only=1` | `INSERT` 抛 `OperationalError` |
| 路由层 | 蓝图级 `before_request` 拒非 GET（唯一例外 `POST /m/pair`）；独立 app 天然 404 | `test_mobile_readonly.py` |
| 口径层 | 余额只走 `get_customer_account_ledger()`（`customer_balance_cents()` 在 `app/` 内零调用） | `customer_balances(ids)` 逐客户对拍 |

`busy_timeout` 不是可选项：主应用 `threads=8` 且用 `BEGIN IMMEDIATE` 写，普通连接读会撞
`database is locked`。

## 两个「必须给现有函数加接缝」的地方

这两处是实现时才暴露的，直接复用会出现「环境对了、代码错」：

1. **`routes/purchases.py:_purchase_today_history()`** 内部 `url_for("purchases.purchase_detail")`。
   在独立 app 里没有 `purchases` 蓝图 → `BuildError`。加可选 `detail_url_builder`，
   默认行为不变，手机端传自己的 URL 生成器。**SQL 不复制第二份。**
2. **`services/accounting.py:get_customer_account_ledger()`** 原来自带 `get_db()`（可写连接 + commit），
   与「手机端所有查询走 mode=ro」冲突。加 `conn=None` 关键字接缝：传了就用调用方的连接。
   余额口径仍只有这一份实现。

## 口径复用清单（手机端不许自创）

| 手机端页面 | 复用来源 |
|---|---|
| 今日 4 类 | `_today_history()`（销售/退货）+ `_purchase_today_history()`（拿货/退拿货）合并；状态过滤逐表照抄 |
| 客户余额/流水 | `get_customer_account_ledger()`（含 `conn=` 接缝） |
| 列表批量欠款 | 新写的 `customer_balances(ids)`：一条 SQL、相关子查询，口径与上面逐项一致 |
| 日期筛选 | `routes/orders.py:_resolve_date_range()`（年/月/起止 → `start_date`/`end_date`） |
| 四表列表 | 抄 `routes/orders.py:370-388` 的 UNION（`business_date` 别名统一两种列名） |
| 名称检索 | `utils/pinyin.py` 的 `name_match_clause()` / `name_match_params()` |

金额显示：2026-10-17 晚用户改口后，列表与详情都显示精确金额（`money_short()`）；旧的 `band()` / `amount_band()` 分档已从手机列表移除，别再照旧文档写「列表只给分档」。

## 商品详情与客户专属价（2026-10-17 补，晚二次修订）

- 商品价 tab 是**商品优先**：`product_price_summary(q)` 一条 `GROUP BY` 查询返回每个商品的客户价数量与最小/最大价；列表卡片只给「N 个客户 + 价格区间」，**不要再先选客户**。
- 双向查询：`product_customer_prices(product_id)`（商品 → 每个客户价，价格降序）与 `customer_price_list(customer_id)`（客户 → 有价商品）。客户详情「看全部」走独立 `/m/customers/<id>/prices`，不再跳商品列表。
- 与桌面端商品编辑页「客户专属价」同源：`customer_prices JOIN customers`；**不额外过滤已删客户**（桌面端也没过滤），否则两边会少一行。已删客户仍列出但标「已删除」、不给链接（点进去会 404）。
- `updated_at` 是 UTC（`CURRENT_TIMESTAMP`），显示走 `_local_stamp()`；不要用 `timestamp_label()` 直接套（它按本地时间解析，不会再做 UTC→本地转换）。
- 「最近成交价」口径与商品价列表一致：该客户最近一张 `saved/printed` 销售/退货单里该商品的单价；拿货/退拿货不计。
- 路由 `/m/products/<id>` 只 GET；`POST` 405，蓝图外 `/products/` 仍 404。

## 金额显示（2026-10-17 晚改口）

用户看到实物后要求列表直接给具体数字。客户列表、单据列表、客户详情头部都走 `money_short()`，不再使用 `band()` / `amount_band()` 分档；工具函数仍保留，但手机列表不再调用。恢复分档必须重新征得用户同意。

## 壳内改地址（2026-10-17 晚）

- 右下角齿轮已删除（和「设置」tab 重复）。设置页 `#addressRow` 与配对页 `#pairChangeAddress` 都通过 `window.SimpleERPNative.openAddressDialog()` 调原生对话框；浏览器里没有桥，入口保持静态/隐藏。
- 配对页没有底部 tab，地址填错时没有别的出口——新增/删除地址入口时，这两个页面都要留退路。
- targetSdk 33+ 的返回键必须注册 `OnBackInvokedCallback`；否则系统走预测返回，`onBackPressed()` 不触发，详情页按返回会直接回桌面。

## 前端实现要点

- 服务端渲染多页应用，**没有**页面转场；只做四处动效（骨架 shimmer / sheet 上滑 / `:active` 缩放 /
  主题过渡），`prefers-reduced-motion` 全关。
- 导航等待：点击后 **150ms** 才显示骨架（局域网常 100ms 内返回，立刻挂骨架会闪），**5 秒**没回
  换成「连不上」+ 地址 + 三条原因 + 重试。电脑关机/不同 WiFi 时请求到不了服务器，浏览器自己的
  错误页接管，我们无法定制。
- **下拉刷新的「顶部」判断看 `.body.scrollTop`，不是 `window.scrollY`**：`.app` 高 100%、滚动发生在
  `.body`，`window.scrollY` 恒为 0；只看它会让列表滚到中间也触发。列表在中间时 touchstart 直接放弃接管。
  手势只在 `'ontouchstart' in window` 时注册；松手触发复用 `#navSkeleton`；300ms 内第二次忽略；
  手势结束后 400ms 内吞掉补发的 click（否则会顺手点进卡片）。
- `.body` 必须 `min-height: 0`：flex 子项默认 `min-height:auto`，内容一长就撑破容器、列表钻到
  tab 栏下面。
- 主题三选取值只存 `localStorage`（键 `erp-mobile-theme`，值是 `system|light|dark`），首帧前用
  内联脚本写 `data-theme`，避免切深色闪白。手机端**没有任何写端**，这是「设置页无写操作」的实现方式。
- 壳侧底色必须与页面一致：`windowBackground=@color/page_bg`（values / values-night）、Activity 同步
  window / root / 状态栏 / 导航栏、WebView 也要 `setBackgroundColor(pageBackground())`；深色 theme 用
  `Theme.Material.NoActionBar`，WebView 的 `prefers-color-scheme` 才会跟系统变深。浅色首帧是
  `#FBFBFD`、深色 `#0B0F16`，不是纯白。
- 页头宏的 `aside` 参数是模板自己拼的内联片段（band 标签、日期按钮），必须 `|safe`；
  标题/副标题保持默认转义，客户名不会被当标记执行。

## 交付与运维事实（真机验过的）

- **防火墙弹窗的时机**：安装与启动都不弹，**首次开启「手机访问」让 EXE 监听 5001 时才弹**；
  点「允许」后生成两条入站 Allow 规则（TCP/UDP），手机即可连。源码模式（`.venv-win\Scripts\python.exe`）
  **不会弹**——socket 实际由 venv 背后的基础解释器持有，它早就有允许规则，别拿源码自测当证据。
- **重装/升级不丢配对**：设备记录在 `data/mobile_devices.json`（数据根内，卸载不删），手机 cookie 在浏览器里；
  重装后扫码直接进、不再要配对码是预期行为。要断开只能在桌面端撤销设备（手机端没有写端）。
- **同一台手机重复配对会多出记录**（无设备指纹），设置页看到两条「Android 手机」正常，撤销多余的一条即可。
- **主机地址可能被代理网卡抢**：本机有 TUN 代理时会拿到代理网段假地址，二维码就废了。
  `lan_ip()` 已按私网优先级过滤，取不到可用地址时**不生成二维码**并提示检查 Wi-Fi。

## 验证方法（可照抄）

```bash
# 全量回归（先确认 5000 没被运行中的桌面版占着）
.venv-win/Scripts/python.exe -m pytest tests/ -q

# 手机端专项
.venv-win/Scripts/python.exe -m pytest tests/test_mobile_*.py -q

# 真实渲染验收：造 200 条演示数据到私有数据根，渲染静态快照，再截图
.venv-win/Scripts/python.exe packaging/seed_demo_data.py --data-root <私有目录> --force
# 然后用 Flask test client 渲染各页 → 把 /static/ 换成 file:/// 绝对路径 → iframe 包 390×844 → 截图
```

截图必须走 iframe 包装：headless Chrome 的窗口宽度被夹到最小 **500px**（`vw=500x749` 实测），
直接 `--window-size=390,844` 出来的是「500 宽布局裁到 390」，会把正常布局看成右侧溢出。
详见 `memory/pitfalls.md`。

## 端口与防火墙

- 端口**固定 5001**，被占就明确报错 + 开关回弹，不自动漂移（端口是配对 base_url 的一部分）。
- Windows 上「端口被占」不能用 bind 异常判定（`SO_REUSEADDR` 允许重复绑定），要用 connect 探测。
- 首次监听时 Windows 防火墙必弹窗，点「取消」会被记成阻止且不再询问 → 症状是「手机永远连不上、
  电脑看不出异常」。设置页「诊断信息」就是为这个场景准备的（监听状态 / 端口自检 / 最后一次成功请求）。
