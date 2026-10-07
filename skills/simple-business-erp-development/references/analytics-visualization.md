# 数据分析页可视化与筛选口径

## 适用范围

`app/erp/templates/analytics/index.html`、`app/erp/static/analytics-v4.css`、`app/erp/static/analytics-controls.mjs`、`app/erp/routes/analytics.py`、`app/erp/services/analytics.py`。改这些文件前先读本页，避免把已确认的口径改回去。

## 顶部筛选是唯一时间基准

- 三种时间模式：`date_mode=range|month|year`。`range` 用 `start_date`/`end_date`；`month` 用 `<input type="month">` 提交的 `start_month_raw`/`end_month_raw`（**YYYY-MM 格式**）；`year` 用 `year`。
- 路由同时兼容旧的 `start_month`/`end_month`（只有月份数字）——解析统一走 `_parse_month_field`，**回显值也要用它**，否则面板会显示成当月。
- 隐藏的时间面板必须同时 `hidden` 且 `disabled`，否则被禁用的输入仍会提交旧值。
- 缺省（参数缺失）才回落到本月；**显式空日期表示不设边界**（历史约定，不要改成回退本月）。
- 热力图**没有独立年份下拉**：它渲染顶部范围内的每一个自然月。`heat_year` 参数已删除，旧链接里的该参数被忽略而不是报错。

## 利润口径 vs 单据流水口径

这是本页最容易改错的地方：

| 指标 | 参与的单据 |
|---|---|
| 净销售额 / 销售金额 / 退货金额 / 毛利润 / 毛利率 | 销售 + 退货（**永远**） |
| 产品排行 / 具体产品分析 | 销售 + 退货（**永远**） |
| 流水热力图 / 「单据流水」卡片 | 四类：销售+ / 退货− / 拿货− / 退拿货+ |

- `summarize_analytics(document_types=[...])` 只影响**流水**部分；`document_types` 为空集表示四类全部。
- 拿货/退拿货没有客户售价与毛利，因此**不能**进入利润类指标；页面上必须保留「不参与净销售额与毛利润」的说明。
- `flow_totals` 提供 `purchase_amount_cents` / `purchase_return_amount_cents` / 各自单据数与 `net_flow_cents`。

## 三个可视化组件的约定

- **月历热力图**：周一起始，`month.leading = date.weekday()` 个空白格；格子内**只放日期数字**，金额/状态放在 `title` 与 `aria-label`。深浅沿用 `--erp-heat-0..4`；负值用 `inset box-shadow` 描边而不是换底色，否则会破坏「深浅=强度」的语义。
- **竖向柱排行**：`_rank_bar_rows` 按**同一张榜内最大绝对值**等比缩放，柱高写 `bar_height_px`（最高 `RANK_BAR_MAX_HEIGHT_PX=190`，其余按比例），数值标注 `position:absolute; bottom:<柱高>px` **贴在各自柱顶**——不要给所有柱一个等高槽框、把标注统一顶到同一条线，那样矮柱的数值会远离柱顶。数量榜（热销/低销量/退货抵销）**全品类同一张榜、不按单位分组**，只按净销售数量排序，因此**数值标签必须带单位**（`18 套`），否则「1 米」和「1 个」无法区分。柱下商品名截断 + `title` 给全名。每组柱数由 `rank_size`（10/20/50/all）控制，旧的 `rank_page`/`rank_unit`/`rank_full` 分页参数已删除。
- **CSS 级联陷阱（本页踩过）**：横向条形（成本缺失来源）与竖向柱**共用 `.rank-bar-track/.rank-bar-fill/.rank-bar-value` 类名**。横向规则若写成裸 `.rank-bar-track{background/border/border-radius}`，会以更低特异性把这三项**泄漏**到竖向柱上（表现为「去掉了槽框但灰底和上边框还在」）。横向一律限定为 `.rank-bar-row .rank-bar-track` 等；竖向 track 显式写 `background:none;border:0;border-radius:0` 兜底。改完必须**禁用缓存**读 computed（`Network.setCacheDisabled` + `Page.reload{ignoreCache:true}`），否则 `?v=1` 的 CSS 会被磁盘缓存命中、读到的仍是旧值。
- **筛选不跳顶**：`analytics-controls.mjs` 里 `rank_key`/`rank_size` 走 `refreshInPlace`——fetch 整页后只替换带 `data-analytics-fragment` 的区块（`analysisMetrics`/`documentFlow`/`layeredAnalysis`/`productAnalysis`），保留滚动位置并 `replaceState` 同步 URL；其余筛选仍整页 `location.assign`。因此：**新增展示参数必须同时加进 `VIEW_KEYS`**，否则 `withViewState` 会抛错并被 catch 成整页跳转（表现为「筛选后跳回顶部」的旧 bug 复发）；**新增结果区块必须带 `data-analytics-fragment`**，否则刷新后是旧数据。区块内的事件一律用**事件委托**挂在 root 上（区块会被替换，直接 `addEventListener` 的监听器会随旧节点消失）。
- **成本诊断**：三层＝覆盖堆叠条（已知 vs 未覆盖）、毛利构成条（成本 + 已知毛利）、缺失来源条形（降序）。成本完整时不渲染「未覆盖」段；范围内无销售时整组图表不渲染，只留文字汇总。窄屏（≤1100px）隐藏堆叠条内的文字，靠图例表达。

## 验证方式

- 定向：`pytest tests/test_analytics_center.py tests/test_analytics_v4_behavior.py tests/test_quantity_display.py`
- 前端：`node --test tests/frontend/`（`analytics_controls.test.mjs` 会断言展示键白名单，新增 URL 参数必须同步 `VIEW_KEYS`）
- 真实页面：用隔离数据根启动预览，用 CDP 读几何而不是看截图——重点核对**日期落位列**、**堆叠条宽度和是否为 100%**、**各宽度横向溢出为 0**。
