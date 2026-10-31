# ERP UI Extension — References 索引

`SKILL.md` 是流程骨架（Procedure 1–11 + Always-On 总纲）；本目录是各专题的展开和实测结论。

**读取方式**：先按「要改什么」在下面定位，只读命中的那一份，不要整目录读完。

## 流程与交付

| Reference | 什么时候读 |
|---|---|
| [baseline-extension-checklist.md](./baseline-extension-checklist.md) | 新建页面，或往已验收页面上加东西之前 |
| [document-scope-and-display-rules.md](./document-scope-and-display-rules.md) | 决定某类单据该显示什么、不该显示什么时 |
| [document-list-and-cross-document-links.md](./document-list-and-cross-document-links.md) | 改单据列表页，或加/改跨单据跳转链接时 |
| [import-and-completion-flags.md](./import-and-completion-flags.md) | 碰导入逻辑，或「待补全 / 需处理」这类完成标志时 |

## 渲染与交互

| Reference | 什么时候读 |
|---|---|
| [template-styling-and-theming.md](./template-styling-and-theming.md) | 改任何模板的样式或主题之前（最大的一份，按小节挑着读） |
| [in-place-fragment-refresh.md](./in-place-fragment-refresh.md) | 接片段刷新、就地更新、局部重渲染之前 |
| [autocomplete-suggestion-reliability.md](./autocomplete-suggestion-reliability.md) | 改自动补全、建议列表、键盘选择时 |
| [rendering-and-layout-measurement.md](./rendering-and-layout-measurement.md) | 声称版式/尺寸/层叠/可读性达标之前；任何几何测量 |
| [filter-list-table-interaction.md](./filter-list-table-interaction.md) | 碰筛选卡、列表页、表格操作列、行内展开、可输入的 select 时 |

## 状态、失败与验收

| Reference | 什么时候读 |
|---|---|
| [state-and-data-consistency.md](./state-and-data-consistency.md) | 从别的字段派生值、加状态标志、插入合成行时 |
| [action-failure-surfacing.md](./action-failure-surfacing.md) | 接任何可能失败的操作时 |
| [acceptance-and-evidence.md](./acceptance-and-evidence.md) | 报缺陷之前，或宣布需求达标之前 |
| [document-print-pagination.md](./document-print-pagination.md) | 让长单据打印、分页之前 |

## 审计脚本

| 脚本 | 用途 |
|---|---|
| [../scripts/audit-nav-and-scroll.mjs](../scripts/audit-nav-and-scroll.mjs) | 导航与滚动行为 |
| [../scripts/audit-control-centering.mjs](../scripts/audit-control-centering.mjs) | 控件居中测量 |
| [../scripts/audit-glass-depth.mjs](../scripts/audit-glass-depth.mjs) | 面板层叠深度测量 |

三个脚本都按 `node <script>.mjs <url> [cdp-port]` 跑，需要页面已在调试端口上打开。

## 维护约定

- 新增实测结论写进对应专题，不写回 `SKILL.md` 的 Always-On 段——那一段已经拆干净了，再堆回去就是 81K 的来源。
- 只有**跨专题都成立**的总纲级规则才留在 `SKILL.md`。
- 一个专题只承担一个责任；专题变大就再拆，并在本表加一行。
