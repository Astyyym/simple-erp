# 单据管理与货款汇总导出（2026-07-12）

用户可见「单据查询」改名为「单据管理」。账款管理页可保留收款/余额，**不要**为了加导出而删除 `/accounts/`。

## 导出汇总表

- 入口在单据管理筛选区：**导出汇总表**（= 原打印汇总表）
- `target=_blank` 新开 PDF 预览，不替换筛选页
- 路由示例：`GET /orders/summary_pdf`；导出 URL 可去掉 `order_type`

### 口径

- 有效 `saved`/`printed` 且未删除的 **销售 + 退货** 都进表（兑账付款）
- **故意忽略**列表「只看销售/只看退货」

### 客户范围

| 情况 | 行为 |
|------|------|
| 唯一客户（精确或唯一模糊） | 单段汇总 |
| 空客户 / 模糊多命中 | **允许导出**；按钮旁中文提醒；PDF **按客户分段**（有单据客户，按名排序） |

PDF 模板用 `sections[]`；单客户 API 可委托多客户生成。

## 按月 = 月到月（可跨年）

- 控件：开始年/月 + 结束年/月
- 范围：开始月 1 日 ～ 结束月最后一天
- 允许跨年；起止颠倒归一化
- **不要**再做成只能选单月

## 汇总 PDF 布局坑（单号溢出）

用户验收截图：`DEMO-SALE-20260701` 一类长单号压进「名称规格」。

根因：单号列约 29mm + `.order-no-cell { white-space: nowrap }`。

修复约定：

- 单号列宽 ≥ **42mm**（可从名称/金额列匀一点）
- 单号允许格内换行：`word-break: break-all` + `white-space: normal`（**禁止 nowrap**）
- 字号略小 + 等宽字体；日期列可仍 nowrap
- 改完必须 **真实再生 PDF** 目视单号格；勿只靠模板字符串断言

## 共享筛选卡（单据管理 / 数据分析 / 回收站类型行）

两页筛选卡**必须共用 `base.html` 的 `.filter-card`**，不要在 `orders/list.html` 或 `analytics-v4.css` 里各写一套（历史上两套漂移过：3 列 vs 4 列、时间模式顺序相反、圆角/间距不同）。

结构约定：

```
.filter-card                      4 列满行网格（≤1500px → 2 列，≤1100px → 1 列）
  .filter-field-date              grid-column:span 2（日期面板；[data-mode] 控制显隐 + 同步 disabled）
  .filter-field-product           grid-column:span 2
  .filter-field-type              grid-column:1/-1，内部：
    .filter-type-row              标签 + .filter-type-options + .filter-type-bulk **同一行**
    .filter-type-hint             说明文案（不要塞进 row 里，会破坏「同行」判定）
  .filter-actions                 整行，.filter-actions-extra{margin-left:auto} 让导出组贴右
```

- **订单类型批量按钮**：`标签 + 四类复选框 + 全选 + 清空` 同一行**左对齐**，四类→全选间距 8px。禁止 `justify-content:space-between` / `flex-end` 把全选/清空推到卡片最右（用户明确否决过）。
- **时间模式顺序统一**：`按年 → 按月到月 → 按日到日`，两页一致。隐藏面板必须同时 `hidden` + `disabled`。
- **只统一外观与字段顺序**，不统一控件本身（单据管理客户是输入框+自动匹配，数据分析是下拉）——用户明确只要外观一致。
- 验证：CDP 读几何时**用垂直中心判定「同一行」**（flex `align-items:center` 下不同高度元素顶边本来不齐，用顶边判会假失败）；并断言「全选右缘距卡片右缘」随视口宽度增长（证明没贴最右）。

## 回收站：左列合并单据表 + 类型筛选

结构＝左列一张**四类单据合并表**（类型徽标 + 类型筛选 + 一套批量操作），右列商品、客户两张卡（`.recycle-layout` → `.recycle-side`）。宽屏 1.9fr/1fr，≤1280px 单列。

- **四类清理规则不同，不能一刀切**：恢复对所有类型可用；永久清理统一走 `physically_deletable_order(conn, kind, id)` 的真实引用检查（拿货正式/已作废不可删、退拿货仅无生效历史且无引用的草稿可删）。不可清理的行渲染 `disabled` 的「不可清理」按钮，而不是隐藏。
- **统一批量端点** `POST /recycle/bulk`：勾选键为 `类型:id`（`sale`/`return`/`purchase`/`purchase_return`/`product`/`customer`），按前缀映射到内部 kind；退拿货版本号按 `version_<id>` 单独提交（**顺序无关**，不要依赖 ids 与 versions 的下标对齐）。版本不符 → 整批 400 拒绝，不做部分生效。
- 类型筛选：不勾选 = 显示全部；全选复选框**只作用于当前筛选可见的行**，避免误操作被隐藏类型。

### 批量按钮的绑定契约（踩过坑）

三张卡片必须统一由 `data-recycle-section`（含 `data-recycle-checkbox` / `data-recycle-form`）+ `data-recycle-submit` 驱动一段共享 JS，**不要**再用全局 `submitSelected()`。

历史缺陷：商品/客户卡片的勾选框名为 `product_ids`/`customer_ids`，而 `submitSelected()` 只认 `name="ids"`，两个 form 恒为空 → 按钮恒弹「请先选中要操作的记录」，只能逐条操作。改卡片布局时删掉了旧的 `syncRecycleChecks()` 是直接来源。

- 商品/客户提交到按类型端点 `/recycle/bulk/<product|customer>/<restore|purge>`；单据卡片走合并端点 `/recycle/bulk` 并额外注入 `action` 字段。
- 每次提交前 **重建 form 内容**（`form.innerHTML=''`），否则重复点击会累积上一轮的隐藏 `ids`。
- **这类缺陷 pytest 覆盖不到**：模板里的 JS 不执行就等于没测。改动回收站批量逻辑必须同步跑 `tests/frontend/recycle_bulk.test.mjs`（Node 真跑模板 JS），并在隔离浏览器里真点一次并抓 POST 端点与载荷。

## 单据删除契约：入口直删，底层自动冲回

**当前产品规则（2026-10-07 用户决策）**：列表与详情页**没有**独立的「作废」入口，正式单据**可以直接勾选删除**。但这只是入口与文案的简化，**底下的守恒机制必须保留**：

- `/orders/bulk_delete`、`/orders/{id}/delete`、`/purchases/{id}/delete`、`/purchases/return/{id}/delete` 对 `saved`/`printed` 单据先调 `void_*` 反向过账，再软删除进回收站。
- 固定原因集中定义为 `accounting.AUTO_REVERSE_REASON = "列表删除（自动冲回）"`，界面不填原因。
- 组合服务：`accounting.void_then_delete_order` / `inventory.void_then_delete_purchase_order` / `purchase_returns.void_then_delete`。
- 草稿没有过账，直接进回收站，不写自动冲回原因。
- 回看页/详情页不再渲染 `order-void-row` / `page-danger-action`；`/orders/{id}/void`、`/purchases/return/{id}/void` 路由**保留**（内部与测试使用），删 UI 不等于删路由。
- 拿货/退拿货详情「移入回收站」对正式单据也直接可用。
- 行内删除必须按类型分派（`o.delete_url`）。历史缺陷：四类行都指向 `/orders/{id}/delete`（销售/退货表），拿货/退拿货会打错表。

### 不要再加回「末笔 / 有效退货」限制

**2026-10-07 已按用户决策移除**，不要重新引入：

- `void_order` 原有「必须是该商品最后一笔库存业务，不能作废」与「销售单存在有效退货，不能作废」两道校验已删；`void_purchase_order`、`purchase_returns.void` 的同类「末笔」校验同样已删。
- 用户口径：**移动均价/成本只是内部预期参考**，不据此定售价，且**不进任何对外打印**（`orders/print_template.html` 只有 序号/名称规格/单位/数量/单价/小计；`pdf.py` 无成本字段）。用户原话「它本来就是移动均价，那移动就移动呗」。
- 代价是已知且接受的：删非末笔单据时均价会按移动加权平均重算（可能漂），但**库存数量、销售金额、账款余额精确**等于该单从未存在的值。实测见短计划。
- **必须保留**：`库存不得为负` 的守恒校验；`有历史账务的客户不能物理删除`（账务追溯底线，不是顺序限制）。
- 原销售单被删后，退货单的 `source_order_id` 仍指向已删 id；详情页靠 `source_order_available` 判断，显示「（原单已删除）」而不是死链接。

**列表页 `.btn` 居中规则**：`.btn` 必须自带 `display:inline-flex;align-items:center;justify-content:center`。Bootstrap 的 `inline-block` + `min-height` 在 flex/grid 父容器里会被 blockify 成 `block`，`min-height` 富余全堆到内容下方、文字整体上浮（实测最大 −3px、313 个按钮中 281 个偏移）。验证方式：给按钮插一个继承 `line-height` 的隐藏空格 span，比较 line-box 上下留白之差，阈值 0.6px。

**操作列必须占住宽度，否则按钮被裁（2026-10-07 修复）**：`stable-table` 是 `table-layout:fixed` + `td{overflow:hidden}`。若把 `action-wrap` **直接写在 `<td>` 上**，这一列只会分到「剩余宽度均分」的零头——回收站商品/客户卡片实测 1600px 宽下操作列仅 **66.9px**，「确认删除」被裁 **76.1px**，几乎点不到。

正确写法（与 `orders/list.html` 一致）——**两处都要改**：

```html
<tr><th class="action-cell">操作</th></tr>
...
<td class="action-cell"><div class="action-wrap">…按钮…</div></td>
```

**只改 `<td>` 无效**：`table-layout:fixed` 下**首行（thead）决定列宽**，表头不带 `action-cell` 时 td 上的宽度会被忽略（本轮实测 td 仍只有 128.8px）。修好后四档宽度（1100/1280/1440/1600）三张卡片 `clipPx` 均为 **−7px**（负值＝按钮完全落在单元格内）。

**验证 UI 改动前，先确认服务端吐出的 HTML 已更新**：本轮改完 `recycle/index.html` 后重跑浏览器测量，数值与改前**一模一样**，差点误判成 CSS 写法不对——实际是 Flask 进程的**模板缓存**（`curl` 该路由发现服务端仍吐 24 个 `action-wrap`，磁盘已是 4 个 `action-cell`）。顺序应为：① `curl` 路由 grep 关键类名确认服务端已更新 → ② 再跑浏览器测量。另注意 `TEMPLATES_AUTO_RELOAD=True` 作为环境变量**不生效**，需在 app 上同时设 `app.config['TEMPLATES_AUTO_RELOAD']` 与 `app.jinja_env.auto_reload`；最稳的是改完**重启进程**。

## 回收站永久清理契约（2026-10-07 修复）

**背景**：用户反馈「删到回收站后清不掉，只能一直躺在里面」。根因是 `physically_deletable_order` 要求 `status=='draft'`，而正式单据删除走「先作废再软删除」、状态恒为 `void`，判据**永远不成立** → 回收站只涨不减；**七天自动清理共用同一判据**，因此一起失效。

**现在的规则**：判据改为「**在回收站 + 无活引用**」即可清理，不要重新加回 `status=='draft'`。

- 永久删除顺序：**明细 → 该单据自己的库存流水（含 `:void` 反向流水）→ 单据本体**，不留孤儿。
- **引用顺序自动满足**：`order_items.source_item_id` 是自引用（退货明细 → 销售明细），所以必须先删引用方。`_purge_orders_in_safe_order` 用**多趟扫描**，每趟只删「当前已无活引用」的，剩下的下一趟通过——调用方不需要自己排序。
- **单条删除被仍在回收站的记录引用时**，抛 `PurgeBlockedByRecycledReference` → 转可读 400（**不是 500**），提示「还有回收站记录引用它的明细」。这与「被**活**单据引用」是两件事：后者是用户需先处理的业务事实，前者只是清理顺序问题。
- **批量 purge 必须按类型分流**：单据走 `_purge_orders_in_safe_order`，商品/客户走逐条 `_purge_one`。曾把三种 kind 都塞进单据专用函数，导致商品/客户批量失效（测试抓到）。
- **商品删除**要求先删引用它的单据（活单据依赖库存流水做反向过账）；**有历史账务的客户**始终不物理删除。

## 错误呈现契约：不要返回裸文本

**2026-10-07 修的 bug**：业务校验失败时后端 `return str(exc), 400` 把中文原因当纯文本返回，浏览器直接把文字当页面显示——用户看到只有一行字的空白页、界面丢失、不知为何失败。

**规则**：所有会因业务校验失败而中断的 POST 路由，统一用 `app/erp/utils/errors.py:error_response(message, title=..., back_url=...)`：

- `X-Requested-With: fetch` 或 `Accept: application/json` → JSON `{"error": ...}`；
- 浏览器导航 → 渲染 `error.html`（继承 `base.html`，带侧栏、标题、真实原因、「返回」按钮）。

前端约定：

- 行内删除（`form.ajax-delete-form`）走 `postAndRemoveRow` → 失败时**解析 JSON 的 error** 并就地插入 `.row-action-error` 提示条（不跳页、不用 alert、不吞原因）。
- 禁止新增 `alert('删除失败，请刷新页面后重试')` 这类固定文案——它把后端原因丢掉，用户无法自助处理。
- 批量操作不要「遇到第一个失败就整体中止」，也不要静默跳过：逐条处理并把失败明细拼进提示（`已删除 N 条；以下 M 条未能删除——…`）。

**静默失败陷阱（客户 purge 踩过）**：`DELETE ... WHERE id=? AND <guard>` 在 guard 不满足时影响 0 行且**不报错**，照样 302 + 写审计，用户看到「点了删除、页面刷新、东西还在」。凡是带条件 `DELETE`/`UPDATE` 的清理路径，都要检查 `cursor.rowcount` 并在为 0 时 `raise ValueError(可读原因)`。

## 测试要点

- 跨年月到月起止正确
- 唯一/多客户 PDF 且头为 `%PDF`
- 带 `order_type=sale` 导出时汇总仍含退货
- 页面有「单据管理」「导出汇总表」与无唯一客户提醒
- 模板含单号列宽/可换行规则（`width:42mm`、order-no 非 nowrap）
