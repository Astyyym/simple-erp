# 数据分析页可视化与筛选口径

## 适用范围

`app/erp/templates/analytics/index.html`、`app/erp/static/analytics-v4.css`、`app/erp/static/analytics-controls.mjs`、`app/erp/routes/analytics.py`、`app/erp/services/analytics.py`。改这些文件前先读本页，避免把已确认的口径改回去。

**2026-10-16（schema v15，用户决策 ②/③）本页整块删除了**：毛利润 / 毛利率汇总卡、`health`「成本诊断」页签、成本覆盖与毛利构成的堆叠条 / 环形两套图、成本缺失来源柱图、`sort=profit|stock`、`rank_key=gross_profit|inventory_alert`、产品表的成本与库存列、`cost_view` 展示参数、以及配套的 `cost-*` CSS 与环形悬停代码。本页现在只有**数量与金额**口径。**不要按旧记忆把这些加回来**；需要恢复必须先走需求确认。

## 顶部筛选是唯一时间基准

- 三种时间模式：`date_mode=range|month|year`。`range` 用 `start_date`/`end_date`；`month` 用 `<input type="month">` 提交的 `start_month_raw`/`end_month_raw`（**YYYY-MM 格式**）；`year` 用 `year`。
- 路由同时兼容旧的 `start_month`/`end_month`（只有月份数字）——解析统一走 `_parse_month_field`，**回显值也要用它**，否则面板会显示成当月。
- 隐藏的时间面板必须同时 `hidden` 且 `disabled`，否则被禁用的输入仍会提交旧值。
- 缺省（参数缺失）才回落到本月；**显式空日期表示不设边界**（历史约定，不要改成回退本月）。
- 热力图**没有独立年份下拉**：它渲染顶部范围内的每一个自然月。`heat_year` 参数已删除，旧链接里的该参数被忽略而不是报错。

## 两套口径：数量金额 vs 单据流水

这是本页最容易改错的地方：

| 指标 | 参与的单据 |
|---|---|
| 净销售额 / 销售金额 / 退货金额（3 张汇总卡） | 销售 + 退货（**永远**） |
| 产品排行 / 具体产品分析 | 销售 + 退货（**永远**） |
| 流水热力图 / 「单据流水」卡片 | 四类：销售+ / 退货− / 拿货− / 退拿货+ |

- `summarize_analytics(document_types=[...])` 只影响**流水**部分；`document_types` 为空集表示四类全部。
- 拿货 / 退拿货没有客户售价，因此**不能**进入净销售额与产品排行；页面上必须保留「不参与净销售额与产品排行」的说明。
- `flow_totals` 提供 `purchase_amount_cents` / `purchase_return_amount_cents` / 各自单据数与 `net_flow_cents`。
- 展示参数白名单（`_analysis_presentation`）：`sort` 只接受 `amount`；`tab` 只接受 `heat` / `rank`；`rank_key` ∈ {`hot_sales`,`low_sales`,`net_returns`,`unsold`}；`rank_size` ∈ {`10`,`20`,`50`,`all`}。越界值直接报「展示条件不合法」而不是静默回落。

## 可视化组件的约定

- **月历热力图**：周一起始，`month.leading = date.weekday()` 个空白格；格子内**只放日期数字**，金额/状态放在 `title` 与 `aria-label`。深浅沿用 `--erp-heat-0..4`；负值用 `inset box-shadow` 描边而不是换底色，否则会破坏「深浅＝强度」的语义。右侧当天明细用 `.detail-list` / `.detail-line`（旧名 `.profit-list` / `.profit-line`，已随成本链改名，改 CSS 时别再写成旧名）。
- **竖向柱排行**：`_rank_bar_rows` 按**同一张榜内最大绝对值**等比缩放，柱高写 `bar_height_px`（最高 `RANK_BAR_MAX_HEIGHT_PX=190`，其余按比例），数值标注 `position:absolute; bottom:<柱高>px` **贴在各自柱顶**——不要给所有柱一个等高槽框、把标注统一顶到同一条线，那样矮柱的数值会远离柱顶。数量榜（热销/低销量/退货抵销）**全品类同一张榜、不按单位分组**，只按净销售数量排序，因此**数值标签必须带单位**（`18 套`），否则「1 米」和「1 个」无法区分。柱下商品名截断 + `title` 给全名。每组柱数由 `rank_size`（10/20/50/all）控制，旧的 `rank_page`/`rank_unit`/`rank_full` 分页参数已删除。
- **横向条形已不存在**：`.rank-bar-row` 系列规则随「成本缺失来源」横条一起删除。竖向 track 仍显式写 `background:none;border:0;border-radius:0` 兜底；若将来再加横向条形，仍需把横向规则限定在 `.rank-bar-row` 前缀下，否则会以更低特异性把灰底/边框泄漏到竖向柱上（本页踩过这个级联陷阱）。改完必须**禁用缓存**读 computed（`Network.setCacheDisabled` + `Page.reload{ignoreCache:true}`），否则带 `?v=` 的 CSS 会被磁盘缓存命中、读到的仍是旧值。
- **筛选不跳顶**：`analytics-controls.mjs` 里 `rank_key`/`rank_size` 走 `refreshInPlace`——fetch 整页后只替换带 `data-analytics-fragment` 的区块（`analysisMetrics`/`documentFlow`/`layeredAnalysis`/`productAnalysis`），保留滚动位置并 `replaceState` 同步 URL；其余筛选仍整页 `location.assign`。因此：**新增展示参数必须同时加进 `VIEW_KEYS`**，否则 `withViewState` 会抛错并被 catch 成整页跳转（表现为「筛选后跳回顶部」的旧 bug 复发）；**新增结果区块必须带 `data-analytics-fragment`**，否则刷新后是旧数据。区块内的事件一律用**事件委托**挂在 root 上（区块会被替换，直接 `addEventListener` 的监听器会随旧节点消失）。
- **条形统计图「竖向生长」动画**（`.is-growing`，2026-10-10；v15 起只剩竖向柱一种形态）：切到**产品排行**时柱子从基线长到终高。**速度恒定（px/秒），不是「所有柱同时长完」**——每根柱时长 = 柱高 ÷ `BAR_GROW_SPEED_PX_S=240`（最高柱 190px≈0.79s），所以矮柱先到顶、高柱用时更长；`linear`，不要加 easing（缓动会让各柱快慢不一致）。CSS 里 `--bar-rise`（终高）与 `--bar-dur`（本柱时长）由 JS 写在柱元素上，柱顶数值 `.rank-bar-value` 同用这两个变量做 `translateY` 上升，保持贴柱顶。
  - **触发点必须在 JS，不能在 CSS**：结果区块由 `refreshInPlace` 整块替换，纯 CSS 动画会在**每次就地刷新时重放**（含不该播的场合）。JS 触发点有**两个**：① `selectTab` 切到 `rank` 且原本未选中；② `applyViewChange` 完成就地刷新后调 `growActivePanel()`（当前页签是 `rank` 才播）——换榜 / 换每组显示 / 点「显示」都重播生长。**整页加载与整页筛选重查不播**；点已选中页签不播。新节点不带 `.is-growing`，因此只有上面两处会播。
  - `prefers-reduced-motion: reduce` 时 JS 直接不加类（CSS 侧再关一次动画）。
  - **验收方法**：页内 `requestAnimationFrame` 采样每根柱 `getBoundingClientRect().height` 与 `animationDuration`，断言 ① 高度从 0 单调长到终值；② 各柱「高度 ÷ 时长」一致（≈240 px/s）；③ 最高柱时长最长；④ 换榜/换每组显示后 `.is-growing` 存在且柱高从 0 长起、整页加载后不存在；⑤ reduced-motion 下 `animationName==='none'` 且高度直接是终值。**注意无头/后台标签会节流 rAF**（`document.visibilityState==='hidden'` 时 rAF 不推进），采样前先 `Page.bringToFront` + `Emulation.setFocusEmulationEnabled`。

## 验证方式

- 定向：`pytest tests/test_analytics_center.py tests/test_analytics_v4_behavior.py tests/test_quantity_display.py`
- 前端：`node --test tests/frontend/*.test.mjs`（**必须给 glob**：Node 26 下 `node --test tests/frontend/` 会把目录当单个测试文件，报 `pass 0 / fail 1`；`analytics_controls.test.mjs` 会断言展示键白名单，新增 URL 参数必须同步 `VIEW_KEYS`）
- 模板内联 JS：把模板里的 `<script>` 抽出来交 `node --check`（Jinja 表达式替 `0`、语句标签删掉）。模板一改就该跑一次——删列/删块最容易把 JS 写坏。
- 真实页面：用隔离数据根启动预览，用 CDP 读几何而不是看截图——重点核对**日期落位列**、**各宽度横向溢出为 0**、**产品表 3 列**。
