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

金额显示两条口径：**列表只给分档、详情才给精确**。客户列表用欠款档（无欠款/<1千/1千–1万/>1万）；
单据列表用**中性档**（无金额/<1千/…，按绝对值）——把「无欠款」套在退货单 `-¥300` 上是错的。

## 前端实现要点

- 服务端渲染多页应用，**没有**页面转场；只做四处动效（骨架 shimmer / sheet 上滑 / `:active` 缩放 /
  主题过渡），`prefers-reduced-motion` 全关。
- 导航等待：点击后 **150ms** 才显示骨架（局域网常 100ms 内返回，立刻挂骨架会闪），**5 秒**没回
  换成「连不上」+ 地址 + 三条原因 + 重试。电脑关机/不同 WiFi 时请求到不了服务器，浏览器自己的
  错误页接管，我们无法定制。
- `.body` 必须 `min-height: 0`：flex 子项默认 `min-height:auto`，内容一长就撑破容器、列表钻到
  tab 栏下面。
- 主题三选取值只存 `localStorage`（键 `erp-mobile-theme`，值是 `system|light|dark`），首帧前用
  内联脚本写 `data-theme`，避免切深色闪白。手机端**没有任何写端**，这是「设置页无写操作」的实现方式。
- 页头宏的 `aside` 参数是模板自己拼的内联片段（band 标签、日期按钮），必须 `|safe`；
  标题/副标题保持默认转义，客户名不会被当标记执行。

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
