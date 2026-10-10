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
- **成本诊断**：三层＝覆盖堆叠条（已知 vs 未覆盖）、毛利构成条（成本 + 已知毛利）、缺失来源柱图。成本完整时不渲染「未覆盖」段；范围内无销售时整组图表不渲染，只留文字汇总。窄屏（≤1100px）隐藏堆叠条内的文字，靠图例表达。**成本覆盖 / 毛利构成支持「堆叠条 ↔ 环形」切换**（`cost_view=bar|ring`，默认 bar）：环形两段用 `<circle>` 描边弧（`stroke-linecap:round` 端头圆角、`pathLength=360`），缝隙与圆角伸出量都从各段跨度内预留，故段间恒留 `DONUT_GAP_DEG=7°` 缝且互不重叠；从 12 时起顺时针，靠 mask 里一条白色弧的 `stroke-dashoffset` 由 `sweep_len` 动画到 0 实现「出现」；悬停某段沿该段中角向外位移 `DONUT_POP_PX`。**悬停数值浮标贴在环外缘**（`DONUT_TIP_RADIUS` 算出的中角外侧锚点，绝对定位在 `.cost-ring-view` 上，`.cost-ring-tip.is-active` 才显示）——**不要把数值写进圆心**：圆心 `.cost-ring-center` 恒显示总量，早期版本把悬停值塞进圆心会与同色环带重叠糊成一团。viewBox 取 `-55 -55 330 330` 给外缘浮标留位，故 CSS 宽度给足 `.cost-ring-view{width:260px}`；浮标用合成 mouseover 遍历各段量测 bounding box 确认不溢出容器。**成本缺失来源改为竖向柱**（`.rank-columns.cost-missing-columns`，最高柱 `RANK_BAR_MAX_HEIGHT_PX=190`），不再用横向 `.rank-bar-row`；`bar_percent` 仍保留给旧断言，新模板用 `bar_height_px`。
- **条形统计图「竖向生长」动画**（`.is-growing`，2026-10-10）：切到 产品排行 / 成本诊断 时柱子从基线长到终高。**速度恒定（px/秒），不是「所有柱同时长完」**——每根柱时长 = 柱高 ÷ `BAR_GROW_SPEED_PX_S=240`（最高柱 190px≈0.79s，与环形扫掠 0.8s 同节奏），所以矮柱先到顶、高柱用时更长；`linear`，不要加 easing（缓动会让各柱快慢不一致）。CSS 里 `--bar-rise`（终高）与 `--bar-dur`（本柱时长）由 JS 写在柱元素上，柱顶数值 `.rank-bar-value` 同用这两个变量做 `translateY` 上升，保持贴柱顶。横向堆叠条套不上竖向生长，改 `clip-path:inset(0 100% 0 0)→inset(0 0 0 0)` 从左向右展开（**不要 `scaleX`**，会压扁条内文字），时长按容器像素宽 ÷ `STACK_GROW_SPEED_PX_S=700`。
  - **触发点必须在 JS，不能在 CSS**：结果区块由 `refreshInPlace` 整块替换，纯 CSS 动画会在**每次就地刷新时重放**（含不该播的场合）。JS 触发点有**两个**：① `selectTab` 切到 rank/health 且原本未选中；② `applyViewChange` 完成就地刷新后调 `growActivePanel()`（按当前页签决定播哪块）——**换榜 / 换每组显示 / 点「显示」/ 成本视图切换都重播生长**（用户 2026-10-10 明确要求「这几个切换时也要有动画」）。**整页加载与整页筛选重查不播**；点已选中页签不播。新节点不带 `.is-growing`，因此只有上面两处会播。
  - `prefers-reduced-motion: reduce` 时 JS 直接不加类（CSS 侧再关一次动画）。
  - **验收方法**：页内 `requestAnimationFrame` 采样每根柱 `getBoundingClientRect().height` 与 `animationDuration`，断言 ① 高度从 0 单调长到终值；② 各柱「高度 ÷ 时长」一致（≈240 px/s）；③ 最高柱时长最长；④ 换榜/换每组显示后 `.is-growing` 存在且柱高从 0 长起、整页加载后不存在；⑤ reduced-motion 下 `animationName==='none'` 且高度直接是终值。**注意无头/后台标签会节流 rAF**（`document.visibilityState==='hidden'` 时 rAF 不推进），采样前先 `Page.bringToFront` + `Emulation.setFocusEmulationEnabled`。

### 环形悬停抖动：命中层必须与视觉层解耦（本页踩过两次）

- **现象**：鼠标停在环段的**内侧/外侧边缘**时，高亮与浮标忽亮忽灭、状态卡住不复位（`elementFromPoint` 已在段上但 `hover=false`）。
- **两个独立成因，都要修**：
  1. **清理不彻底**：`mouseover` 有「同段短路」判断 `view.dataset.hoverSeg === label`，但 `mouseout` 只清 `is-hover`／浮标、**没删 `hoverSeg`** → 移开再回到同一段被短路吞掉、浮标不再出现。清理函数必须一并 `delete view.dataset.hoverSeg`。
  2. **反馈回路（真正的根因）**：把外扩动画挂在被判定的元素上（`:hover` 或 JS 给视觉弧加位移）时，段一外移光标就落空 → 触发移出 → 段弹回 → 又命中，形成抖动；且光标不动时不会再触发 `mouseover`，表现为「卡死不亮」。
- **正确修法**：在 SVG 里另加一层**透明加厚命中弧** `<circle class="cost-ring-hit" stroke="transparent" stroke-width="40">`（与视觉弧同 `dasharray`/`dashoffset`），**永不移动**，专门负责 `mouseover`/`mouseout`；视觉弧 `.cost-ring-seg` 设 `pointer-events:none`，只在 JS 给命中段加 `.is-pop` 类做位移与阴影。CSS 用 `.cost-ring-hit{pointer-events:stroke}`。判定层与动画层分离后，光标在段内任何径向位置都稳定。
- **验收方法**：真实鼠标（CDP `Input.dispatchMouseEvent`）沿段中角方向**径向逐点扫描**（含内外边缘），断言 `hover` 序列**单调**、且每点 `hover` 与 `elementFromPoint` 命中的段**一致**；再对同一段做「进入→离开→再进入」确认可重复激活。只看单点成功、或只读几何不驱动真实鼠标，都会漏掉这个抖动。

## 验证方式

- 定向：`pytest tests/test_analytics_center.py tests/test_analytics_v4_behavior.py tests/test_quantity_display.py`
- 前端：`node --test tests/frontend/`（`analytics_controls.test.mjs` 会断言展示键白名单，新增 URL 参数必须同步 `VIEW_KEYS`）
- 真实页面：用隔离数据根启动预览，用 CDP 读几何而不是看截图——重点核对**日期落位列**、**堆叠条宽度和是否为 100%**、**各宽度横向溢出为 0**。
