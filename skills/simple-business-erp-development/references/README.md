# 项目技能 References 索引

本目录用于保存简单ERP专属、可复用的业务规则和实测经验。读取前先判断是**当前规则**还是**历史案例**；当前事实优先级为源码、测试、`VERSION` 与 `docs/需求/消防器材店ERP系统-需求文档.md`。旧路径、旧登录、临时分支和旧打印方案只保留作历史背景，不作为现行指令。

## 当前产品规则

| Reference | 用途 |
|---|---|
| [customer-analytics-and-master-data-import.md](./customer-analytics-and-master-data-import.md) | 客户统计、日期筛选、导入规则与测试边界 |
| [document-management-summary-export.md](./document-management-summary-export.md) | 单据管理筛选、货款汇总 PDF 的统计口径 |
| [excel-data-export.md](./excel-data-export.md) | Excel 导出字段、筛选语义及回归测试 |
| [md-order-number-and-date-lock.md](./md-order-number-and-date-lock.md) | MD 单号、创建日期、重编辑保护规则 |
| [permanent-no-login.md](./permanent-no-login.md) | 当前永久免登录决策与禁止回潮事项 |
| [settings-page-and-data-root.md](./settings-page-and-data-root.md) | 当前设置页与数据根目录行为 |
| [print-offset-calibration.md](./print-offset-calibration.md) | 当前 A4 竖版输出、驱动选项与偏移校准边界 |
| [master-data-cleaning-before-import.md](./master-data-cleaning-before-import.md) | 在项目外安全清洗客户/商品资料的流程，不含真实客户清单 |
| [erp-ui-redesign.md](./erp-ui-redesign.md) | UI 改版约束与视觉回归检查 |
| [purchase-return-decoupling.md](./purchase-return-decoupling.md) | 四类开单解耦、两级商品选择器、手工退拿货与 schema13 迁移；NH 共用日流水取号 |
| [analytics-visualization.md](./analytics-visualization.md) | 数据分析页月历热力图、条形排行、成本诊断三层图与顶部筛选口径 |

## 开发、部署与排障流程

| Reference | 用途 |
|---|---|
| [plan-item-admission-three-questions.md](./plan-item-admission-three-questions.md) | 写计划/需求清单时用「三问」（数据存在？需求真实？已解决？）筛掉无效条目 |
| [agent-managed-dev-preview.md](./agent-managed-dev-preview.md) | Windows/WSL 预览端口、实例确认与链接交付 |
| [user-visible-instance-verification.md](./user-visible-instance-verification.md) | 用户页面、测试结果与真实运行实例的证据等级 |
| [wsl-windows-localhost-instance-mismatch.md](./wsl-windows-localhost-instance-mismatch.md) | Windows/WSL localhost 实例错配排查 |
| [windows-native-exe-packaging.md](./windows-native-exe-packaging.md) | Windows PyInstaller one-folder 构建和 EXE 冒烟 |
| [windows-chinese-path-exe-smoke.md](./windows-chinese-path-exe-smoke.md) | 中文路径下的 Windows 构建与隔离冒烟注意事项 |
| [green-folder-upgrade-and-data.md](./green-folder-upgrade-and-data.md) | 当前数据根、旧绿色目录及安全升级 |
| [versioning-github-backup.md](./versioning-github-backup.md) | 版本、备份、推送、打包和 GitHub Release 的分离核验 |
| [pywebview-save-print.md](./pywebview-save-print.md) | 桌面端异步保存后打开 PDF 的已落地模式 |

## 历史经验（阅读时不得当作当前缺陷/当前功能）

| Reference | 历史边界 |
|---|---|
| [desktop-print-session.md](./desktop-print-session.md) | 旧登录会话导致外部浏览器体验的历史问题；当前永久免登录，仍需区分壳内与外部浏览器 |
| [sales-slip-paper-241x140.md](./sales-slip-paper-241x140.md) | 旧 241×140mm 连续纸版心历史方案；已由 A4 竖版要求替代，不作为当前版式依据 |
| [pywebview-save-print-405.md](./pywebview-save-print-405.md) | 旧 WebView 405 调查步骤，先复现请求再判断是否仍适用 |
| [erp-session-notes.md](./erp-session-notes.md) | 已脱敏的历史流程与 UX 经验；不保存公司/客户真实联系信息 |
| [_archived-full-body-20260725.md](_archived-full-body-20260725.md) | 旧版完整技能归档；不作为当前规则来源，凭据与个人信息不得恢复 |

更新功能后，同时检查 `docs/需求/` 和本目录对应专题。不要为了更新目录而把每份历史会话复盘改写成当前事实。
