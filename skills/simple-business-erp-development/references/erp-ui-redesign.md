# ERP UI 重构参考：成熟企业后台风格

## 适用范围

用于 Flask + Bootstrap + PyWebView 类本地桌面 ERP 的纯视觉重构。目标是保留路由、表单字段、业务逻辑和稳定表格交互，只替换视觉系统与页面层级。

## 推荐参考方向

- IBM Carbon：企业后台信息层级、数据表格、表单、状态色和 8px 间距体系。
- SAP Fiori / Odoo / ERPNext：业务优先的侧边导航、高频入口、低装饰工作台。
- Airtable：清晰的数据录入区域、克制圆角、可读表格和明确操作入口。

应提取通用原则，不复制品牌特征或成品页面。

## 已验证的改造顺序

1. 先读 `base.html`、首页、开单页、列表页和账款页，确认全局样式与页面内联样式的边界。
2. 在 `base.html` 建立统一 CSS tokens：主色、文字、弱文字、边框、背景、表面、危险/成功色、圆角和阴影。
3. 先重构应用壳：固定侧栏、导航分组、激活状态、顶栏、内容区宽度。
4. 再统一 Bootstrap 组件：按钮、输入框、筛选表单、表格、卡片、提示框。
5. 首页改为业务工作台，只放真实高频入口，不制造虚假指标或装饰数据。
6. 开单页保留 Excel 式逐行录入，仅增强信息分组、焦点态、只读金额、联想下拉层和操作区层级。
7. 保留既有测试依赖的用户可见文案，除非同步更新测试；UI 重命名也可能造成语义回归测试失败。
8. 用 pytest 的自动临时数据库 fixture 跑全量测试；确认本次测试未访问正式 `data/erp.db`，不手工搬动/恢复业务库。
9. 启动真实 Web 页面，用浏览器检查工作台与开单页；验证裁切、横向滚动、表格行高、操作列、下拉层和控制台。
10. 用户尚未验收视觉方向时，只保留本地改动，不主动提交、推送、升级版本或重新打包 EXE。

## 必须保持的 ERP 交互约束

- 列表删除/批量操作时，行高、列宽和操作按钮位置不能漂移。
- 开单默认一行；客户与商品联想、客户价/常规价切换、单位与价格可编辑、自动合计全部保留。
- 客户/商品提示必须来自 ERP 数据库，而不是浏览器输入历史。开单表单关闭自动补全，数量、单价、单位、备注等交易字段不应积累并弹出旧值；修改后同时验证 ERP 自有联想仍可用。
- Chromium/WebView 可能忽略普通 `autocomplete="off"`；必要时组合表单级关闭、字段级抗自动填充值和密码管理器忽略标记，并在真实桌面 WebView 中点击字段验证。
- 退货行的警示色应清楚但不过度刺眼。
- 表格优先可读和高密度，不把每行做成卡片。
- 主操作只有一个高强调色；危险、成功、警告使用语义色。
- 不用 Emoji 充当正式 ERP 导航图标；没有统一图标库时宁可用纯文字。

## 客户/商品联想与玻璃卡片、表格滚动容器

- 保留表格横向滚动时，先实测默认单行中的多条建议能否越过表格边界并被鼠标命中；`overflow-x:auto` 会影响另一轴，仅添加 `overflow-y:visible` 不能当作修复证据。
- 在支持的目标 Chromium/WebView2，客户和商品共同使用 top-layer 浮层、定位和关闭控制器；只给商品接入会留下客户菜单被兄弟玻璃卡片盖住的问题。保持原 DOM 和 ARIA 关联，约束浮层宽度/高度与屏幕边界，表格/正文移动或窗口变化时收起，菜单内部滚动不应触发误关。
- 用 `elementFromPoint` 核对菜单跨越下方卡片位置的命中目标，再真实点击读取稳定ID；高 `z-index`、菜单节点存在或接口返回结果都不是遮挡修复证据。EXE验收需操作该EXE的实际WebView2，不拿独立源码浏览器代替。
- 明确两行动作要求时，普通标题动作与危险表单使用两个独立容器；不要依赖flex自动换行。分别测宽窄视口的按钮坐标、最右返回和第二行危险操作，保留必填/version/确认语义。
- 验证鼠标末项、连续方向键移至末项、Enter/Esc、价格/单位及快速重新聚焦。延迟失焦回调须检查当前焦点，关闭菜单须使未完成的网络建议失效。
- 区分商品长列表允许的内部滚动与 PDF 预览完全无横纵滚动条，不能为了统一外观删掉访问列表尾部的能力。

## 毛玻璃（glassmorphism）— 克制企业版（Issue #1 实测）

用户明确要毛玻璃时才做；默认仍偏实色企业后台。目标是**层次感**，不是营销站毛玻璃。

### 允许 / 禁止

| 做 | 不做 |
|----|------|
| `base.html` CSS 变量 + surface 外壳 | 改 `print_template.html` / WeasyPrint |
| 侧栏、顶栏、card、表格外壳、筛选 form | 表单 input 半透明糊字 |
| light / dark / system 各套 glass token | 紫粉渐变、blur >16px、全站氛围光 |
| `@supports not (backdrop-filter)` 实色回退 | 引入 Tailwind / npm / 新框架 |

### 推荐 token（两档）

**首版（克制）** — 用户没说“再强一点”时：

```css
--erp-glass-blur: 12px;
--erp-glass-bg: rgba(255,255,255,.78);      /* dark: rgba(23,30,43,.72) */
--erp-glass-bg-strong: rgba(255,255,255,.90);
--erp-sidebar-glass: rgba(23,32,51,.82);    /* dark ~.80 */
/* saturate ~1.1–1.15；mesh 很淡 */
```

**加强档（用户反馈“再强一点 / 效果不够”）** — Issue #1 二轮实测：

```css
--erp-glass-blur: 16px;                     /* 上限，勿再抬 */
--erp-glass-bg: rgba(255,255,255,.58);      /* dark: rgba(23,30,43,.48) */
--erp-glass-bg-strong: rgba(255,255,255,.78); /* dark ~.70 */
--erp-sidebar-glass: rgba(23,32,51,.62);    /* dark ~.58 */
/* saturate 1.2–1.35；content-mesh 多一层、略亮，才能看出 blur */
```

- 侧栏/顶栏/卡片共用同一 blur token；侧栏可略更透。
- 主内容用 wash + mesh 衬托玻璃；mesh 太淡时毛玻璃几乎看不出。
- **输入框、联想下拉、主 CTA（primary quick-card）保持实色**。
- 页面硬编码 `background:#fff`（index / orders/new / settings-card）改成 glass/surface token。
- 只调 CSS 变量即可加强/减弱，不必重写布局。

### 验证

1. 当前产品永久免登录，页面无需 cookie；`/health` 应返回 `auth: disabled`。检查 `/`、`/orders/`、`/orders/new` 响应和必要交互。
2. HTML 含 `--erp-glass-*` / `backdrop-filter` / 当前档 blur 值。
3. `/`、`/orders/`、`/orders/new` 200，滚动/点击无遮挡。
4. `print_template.html` 无 glass/backdrop/blur 关键字。
5. `PYTHONPATH=app pytest tests/ -q` 全绿。
6. light/dark/system 肉眼看侧栏+顶栏+卡片对比度；加强档后确认表字仍可读。

## 顶栏钉住 + 毛玻璃（用户反馈 2026-07-14，二轮纠正）

用户在 2026-07-14 圈出 `.page-topbar`（公司名、连接状态及当时的退出入口）时，需求通常是：

> 退出入口属于 v0.8.0 之前的历史界面；当前顶栏不应恢复登录/退出控件。


1. **只钉顶栏**（不要把「业务工作台」标题/快捷卡一起 sticky）
2. **滚动时内容不要从顶栏区域露出来**
3. **仍要顶栏毛玻璃**（用户会拒绝「为了不露内容把顶栏改成实色」）

### 根因

- 顶栏在**同一滚动容器**里用 `position:sticky` + 半透明 glass + `backdrop-filter`
- 内容区上 padding / 顶栏负 margin 造成 sticky 上下空隙
- 正文与顶栏共享 scrollport → 半透明时必然「透」出下面内容

### ❌ 错误捷径（本会话踩过）

```css
/* 实色 sticky：能挡内容，但顶栏毛玻璃没了 — 用户会立刻否决 */
.page-topbar { position: sticky; background: var(--erp-surface); backdrop-filter: none; }
```

### ✅ 推荐改法：壳层拆分（钉住 + 保留 glass + 不透内容）

把顶栏移出滚动区；只有正文兄弟容器滚动。

**HTML（`base.html`）：**

```html
<main class="content">
  <div class="page-topbar">…</div>
  <div class="page-body">
    {% block content %}{% endblock %}
  </div>
</main>
```

**CSS：**

```css
.content {
  display: flex; flex-direction: column;
  height: 100%; overflow: hidden; padding: 0;
}
.page-topbar {
  flex: 0 0 auto; margin: 0; padding: 0 30px; min-height: 58px;
  position: relative; z-index: 50;
  background: var(--erp-glass-bg);
  border-bottom: 1px solid var(--erp-glass-border);
  -webkit-backdrop-filter: blur(var(--erp-glass-blur)) saturate(1.3);
  backdrop-filter: blur(var(--erp-glass-blur)) saturate(1.3);
}
.page-body {
  flex: 1 1 auto; min-height: 0; overflow: auto;
  padding: 26px 30px 48px;
}
```

- 顶栏 glass 模糊的是**固定背景 mesh/wash**，不是滚动正文 → 不会「内容从顶栏透出来」
- 窄屏只改 padding（topbar `0 20px`，page-body `20px 20px 32px`），不要再对 topbar 做负 margin 拉滚动缝
- `@supports not (backdrop-filter)` 时 topbar 回退 `var(--erp-surface)` 仍成立

### 侧栏指定色（同会话）

用户给侧栏 **精确 HEX**（例 `#012121`）时：

- 浅/深/system 三套 token 都写成该实色：`--erp-sidebar` / `--erp-sidebar-glass: #012121`
- **关掉侧栏** `backdrop-filter`，否则半透明叠色不等于用户色值
- hover 用略亮邻色（如 `#0a3535`）；active 菜单可仍用 primary 蓝块
- 不要把「侧栏实色品牌色」误推成「顶栏也必须实色」

### 验证

- 长列表往下滚：顶栏贴顶不动；顶栏矩形内**看不到**正文文字/卡片边
- 顶栏仍有 glass 质感（半透明 + blur），侧栏若指定 HEX 则为实色
- 硬刷新 Ctrl+F5；Flask `debug=False` 缓存模板 → **改 CSS 后必须重启 5001** 再探针

## 侧栏宽度与品牌图标（v3.1.0 统一）

- **侧栏宽度全局只有一个值（224px）**。历史上 4 个开单页为给右侧 PDF 预览腾宽度被单独收窄到 200px（`.order-entry-page .sidebar{...}`），导致点开单入口与点其他入口宽度跳变 24px。用户要求一致 → 已删除该覆盖。**不要再引入任何按页面收窄侧栏的规则**；`tests/test_order_entry_visual_structure.py::test_all_pages_share_one_sidebar_width` 会断言 `.order-entry-page .sidebar{` 不出现。
- **`body.order-entry-page` 仍保留**（标记开单页），但**不得再用于侧栏宽度**；如需开单页专属样式，先确认不与侧栏宽度冲突。
- **品牌图标**：`brand-mark` 是 34×34 蓝底圆角方块，内含 20×20 内联 SVG（Keyline Icons `file-spreadsheet`，MIT，`stroke="currentColor"`、`stroke-width="2"`，`color:#fff`）。**不加图标库依赖、不引 CDN**，直接内联 `<path>`；三处保持一致：侧栏 `brand-mark`、favicon（内联 data URI）、EXE 图标（`packaging/简单ERP.ico`）。
- **EXE 图标生成**：`packaging/简单ERP.ico` 由同一 SVG 渲染（headless Edge `--screenshot` + `--default-background-color=00000000` → Pillow 存多尺寸 16/24/32/48/64/128/256）。spec 里 `app_icon = project_root/'packaging'/'简单ERP.ico'` 并给 `EXE(icon=str(app_icon))`。
- **验收方式**：CDP 逐路由读 `.sidebar` 与 `.content` 的 `getBoundingClientRect().width/x`，断言**所有页面 × 所有宽度取值集合为单值**；改 CSS 后必须禁用缓存（`Network.setCacheDisabled`）再读 computed。

## 筛选卡：三段式排列（v3.1.0 后统一）

单据管理（`orders/list.html`）与数据分析（`analytics/index.html`）共用 `base.html` 的 `.filter-card`，已从「一个 4 等分平铺网格」改为**三段式**。改动前的平铺网格不是缺陷，是当时的方案；但两页筛选已按下列约定收口，不要改回等价网格。

- **第一段 `.filter-tier-query`（flex wrap）**：时间（模式按钮 + 对应日期输入合成**同一组合控件** `.filter-field-composite`）| 客户 / 商品 / 型号（`.filter-field-plain`）。切换模式只换组合控件下半截的日期输入，不再让「时间模式」和「日期面板」各占一个网格项。
- **第二段 `.filter-tier-conditions`（虚线分隔）**：订单类型、单据状态多选。**不勾选 = 全部**，所以没有「全选」按钮（全选与不勾选结果相同，属冗余）；只保留「清空」。JS 不再有 `[data-type-all]` / `[data-status-all]` 处理器。
- **第三段**：`.filter-chips`（已选条件，可单独 × 移除）+ `.filter-actions`（主操作在左、导出/重置贴右，`margin-left:auto` 保持不变）。
- **chip 规则**（`app/erp/utils/filter_chips.py`）：时间范围**不生成** chip（它是常驻控件）；四类全勾 / 无选择都不生成 chip；商品 chip 移除时连同 `spec` 一起清（型号从属商品）；移除链接保留其余筛选与展示态、去掉 `page`。默认无筛选时不渲染 chip 行。
- **测试同步**：`test_order_list_filter_layout.py` / `test_ui_layout_reflow.py` / `test_analytics_v4_behavior.py` / `test_orders_type_filter.py` / `test_analytics_center.py` 中编码旧 4 列网格（`repeat(4,minmax(0,1fr))`、`grid-column:span 2`）与「全选」文案的断言必须随结构更新；`tests/test_filter_chips.py` 覆盖 chip 出现/移除/时间与全选不产生 chip。
- **陷阱**：断言 `"全选" in html` 会在删掉按钮后命中 `base.html` 里描述该约定的**注释文本**而假通过。改筛选相关断言时不要只搜可见文案，要断言真实 class / `data-*` 属性。

## 基础资料列表页顶部工具区（商品/客户，2026-10-09 重排）

商品管理（`products/list.html`）与客户管理（`customers/list.html`）的顶部不再各写一份 `form.row` + 等分 `col`，改为共用 `base.html` 的 `.list-*` 类。旧的等分 `col` 按格子分宽而不是按语义分：1280 窗口下「默认单价(元)」占位需 79px、可用 71px 被裁；每行 78px 高只装 40px 控件，行内空、行间只剩 8px。

- **结构**：`.list-import-card`（批量导入，独立卡片）+ `.list-tools`（`.list-search` 查找行 / `.list-facet` 档案筛选 / `.list-create` 新增），块间 `.list-tools>*+*{margin-top}` 用素线分隔。字段用 `.list-create-fields` 等宽网格 `repeat(auto-fit,minmax(180px,1fr))`——宁可整齐换行，也不把 6 个框压到 116px 挤成一排。
- **低频动作默认收起**：批量导入与单条新增都用原生 `<details>/<summary>`（不引 JS，键盘可操作）。切换标题在 `base.html` 统一（`.list-create-toggle` / `.list-import-toggle`，三角箭头靠 `::before` + `[open]` 旋转）。
- **`<details>` 折叠陷阱（实测踩过）**：面板内 `form` 自带 `display:flex`（作者样式）会**盖掉浏览器 UA 对「闭合 details 内非 summary 子元素」的默认隐藏**，收起后表单仍可见。必须显式写 `.list-create-panel:not([open])>*:not(summary),.list-import-panel:not([open])>*:not(summary){display:none}`。只测 `details.open === false` 会漏掉这个缺陷，要测渲染高度/`display`。
- **导出不能跟着导入一起收**：`下载Excel模板` / `导出Excel` 是迁移后高频动作，必须留在 `<details>` **之外**（与 `.list-import-links` 同层）；只有文件上传 + 同名策略 + 预检并导入收进面板。
- **失败/成功要自动展开**：新增失败（路由传 `create_open=True`）和导入失败/完成（模板里 `import_error or import_result or import_init_errors` → `open`）都必须展开面板，否则用户看不到错误原因或结果摘要。路由侧只需在商品新增失败分支传 `create_open=True`；导入两个分支的变量本来就传了，模板内用 `{% set %}` 判定即可。
- **提交按钮改名**：原提交按钮与切换标题同名（都叫「新增商品」），展开后出现两个同名按钮；提交按钮改叫「确认新增」。
- **契约测试**：`tests/test_master_data_list_layout.py` 锁结构钩子（class/元素）而非可见文案；覆盖共用类、默认收起、`display:none` 规则存在、失败/完成自动展开、colgroup 列数、操作列宽度类。

## 备份提示只保留一条，且必须随状态消失（2026-10-09）

商品/客户页原先有两条提示：黄色「尚未创建任何备份」（`{% if not has_backup %}`，有备份即消失）和灰色「迁移旧数据前请先备份」（**无条件常驻**）。灰条内容是黄条的子集，且备份建好后仍永久占 41px，属噪声。

- **合并成一条**：迁移提醒并入黄色告警的正文（「导入或迁移旧数据会直接改动业务数据库」），整条挂在 `{% if not has_backup %}` 下。
- **不要留「始终可见」的提醒条**：一次性动作（导入/迁移）的提醒只该在真正有风险时出现。测试要同时断言两个方向——无备份时出现、建备份后**整条消失**；只断言前半会漏掉常驻噪声。
- `tests/test_backup_restore.py::test_master_data_lists_warn_when_no_backup_exists` 已按此更新；`import_preview.html` 的 `{% if not has_backup %}` 提示保留不变（预览页本就是一次性流程）。
- 实测（1280×820）：有备份时两页表格顶部从 y579 提到 y484，省 95px。

## 客户表 colgroup 与表头列数（2026-10-09 修正）

`customers/list.html` 的 `stable-table` 是 `table-layout:fixed`，列宽**取自首行**。历史缺陷：加了「来源」列却只改了 `<thead>`，`<colgroup>` 仍是 6 列 → 多出的列没宽度，「电话」「期初」表头被截断成「电…」「期…」。

- 改列前先数一遍：`<colgroup>` 的 `<col>` 数必须等于 `<thead>` 的 `<th>` 数（`test_master_data_list_layout.py::test_customer_table_colgroup_matches_header_column_count` 已锁）。
- 列宽要在**最小窗口 1100px** 也不裁表头；当前取值：勾选 44px / 名称 22% / 电话 15% / 地址 26% / 期初 100px / 来源 90px / 操作 190px。改完用 CDP 在 1100/1280/1440/1600 四档读 `th.scrollWidth > th.clientWidth` 复核。
- 断言表体单元格时**必须先造数据**：空 `<tbody>` 里没有 `<td>`，`html.split('<tbody>')[1]` 取到的 body 段是空的，会假失败。
- 注：`th.scrollWidth > th.clientWidth` 对勾选列（16px checkbox + 14px 内边距）会因亚像素取整误报，判断裁切时排除它或按元素 `getBoundingClientRect().right` 比对。

## 验证清单

- 全量测试通过（需隔离正式业务数据库）。
- `git diff --check` 无空白或冲突标记问题。
- 正式数据库文件已恢复。
- 工作台、销售单、退货单、至少一个列表页实际打开。
- 无内容裁切、按钮重叠、异常横向滚动。
- 商品/客户联想下拉不被父容器裁切。
- 多行录入后行高和操作列仍稳定。
- 浏览器控制台无新增 JavaScript 错误。
- 若含毛玻璃：打印模板未污染；input 实色；三主题可读。
- 若用户要求钉顶栏：优先 **content flex + page-body 滚动**，顶栏保留 glass；勿默认改成实色 sticky。
