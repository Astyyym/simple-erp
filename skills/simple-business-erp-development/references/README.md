# 项目技能 References 索引

本目录用于保存简单ERP专属、可复用的业务规则和实测经验。读取前先判断是**当前规则**还是**历史案例**；当前事实优先级为源码、测试、`VERSION` 与 `docs/需求/消防器材店ERP系统-需求文档.md`。旧路径、旧登录、临时分支和旧打印方案只保留作历史背景，不作为现行指令。

## 当前产品规则

| Reference | 用途 |
|---|---|
| [customer-analytics-and-master-data-import.md](./customer-analytics-and-master-data-import.md) | 客户统计、日期筛选、导入规则与测试边界 |
| [document-management-summary-export.md](./document-management-summary-export.md) | 单据管理筛选、货款汇总 PDF 的统计口径 |
| [data-root-probe-pitfalls.md](./data-root-probe-pitfalls.md) | 排查正式数据根时别把 `-wal`/`-shm` 或主库 mtime 当成「有人在写」；只读连接也会创建它们；用 WAL 大小 + 结构指纹判定 |
| [excel-data-export.md](./excel-data-export.md) | Excel 导出字段、筛选语义及回归测试 |
| [md-order-number-and-date-lock.md](./md-order-number-and-date-lock.md) | MD 单号、创建日期、重编辑保护规则 |
| [permanent-no-login.md](./permanent-no-login.md) | 当前永久免登录决策与禁止回潮事项 |
| [settings-page-and-data-root.md](./settings-page-and-data-root.md) | 当前设置页与数据根目录行为 |
| [print-offset-calibration.md](./print-offset-calibration.md) | 当前两套纸型（四类开单连续纸 / 汇总类 A4）、驱动选项与偏移校准边界 |
| [sales-slip-paper-241x140.md](./sales-slip-paper-241x140.md) | 四类开单 241mm 连续纸版式：实体纸 241×140mm、逻辑页 215.9×139.7mm、左右 7.3mm 版心 |
| [master-data-cleaning-before-import.md](./master-data-cleaning-before-import.md) | 在项目外安全清洗客户/商品资料的流程，不含真实客户清单 |
| [product-identity-single-name.md](./product-identity-single-name.md) | **v14 商品身份 = 单一名称**：`spec` 四列已删、迁移三步（预检中止 → 自动备份 → 四表重建）、导入不再接受「型号」列、同类改动的测试适配经验 |
| [name-search-and-pinyin-matching.md](./name-search-and-pinyin-matching.md) | 名称检索统一走 `utils/pinyin.py` 的三个 helper；**检索串必须先归一**（输入法隔音符号 `xiao'fang`、空格、全角）；只输分隔符不能退化成 `LIKE '%%'`；派生列写入/回填与已知边界 |
| [erp-ui-redesign.md](./erp-ui-redesign.md) | UI 改版约束与视觉回归检查 |
| [ui-hint-visibility-toggle.md](./ui-hint-visibility-toggle.md) | 界面说明文字开关（`data-ui-hint` + `html[data-ui-hints]` 一条 CSS）：受控范围、必须可见的边界（危险警告/状态/数据/空态/placeholder）、加标记后的断言写法 |
| [purchase-return-decoupling.md](./purchase-return-decoupling.md) | 四类开单解耦、商品选择器（v14 起单框：销售/退货手输、拿货/退拿货下拉）、手工退拿货与 schema13 迁移；NH 共用日流水取号 |
| [analytics-visualization.md](./analytics-visualization.md) | 数据分析页月历热力图、四类流水口径、产品排行与条形生长动画（v15 起不再有成本诊断层） |
| [mobile-readonly-endpoint.md](./mobile-readonly-endpoint.md) | 手机只读端（浏览器直连，第二监听 5001）：独立 app 结构、只读三层保证、两处必须加的接缝、口径复用清单、前端要点与验证方法 |

## 开发、部署与排障流程

| Reference | 用途 |
|---|---|
| [plan-item-admission-three-questions.md](./plan-item-admission-three-questions.md) | 写计划/需求清单时用「三问」（数据存在？需求真实？已解决？）筛掉无效条目 |
| [agent-managed-dev-preview.md](./agent-managed-dev-preview.md) | Windows/WSL 预览端口、实例确认与链接交付 |
| [user-visible-instance-verification.md](./user-visible-instance-verification.md) | 用户页面、测试结果与真实运行实例的证据等级 |
| [pywebview-webview2-ui-self-verification.md](./pywebview-webview2-ui-self-verification.md) | 桌面壳 UI 可自验：真实 pywebview/WebView2 窗口 + JS 探针 + 只截自身窗口；DOM 状态 ≠ 渲染证据；原生 datalist 画不出 / 自有 popover 画得出（实测） |
| [wsl-windows-localhost-instance-mismatch.md](./wsl-windows-localhost-instance-mismatch.md) | Windows/WSL localhost 实例错配排查 |
| [windows-native-exe-packaging.md](./windows-native-exe-packaging.md) | Windows PyInstaller one-folder 构建和 EXE 冒烟 |
| [windows-bat-crlf-invocation.md](./windows-bat-crlf-invocation.md) | 脚本行尾/编码：`.bat` 需 CRLF（`.gitattributes` 钉死）；含中文的 `.vbs` 需 ANSI/GBK（历史教训） |
| [windows-installer-inno-peruser.md](./windows-installer-inno-peruser.md) | Inno Setup per-user 安装包：为什么不能装 Program Files、config/数据保留策略、静默安装验收 |
| [desktop-splash-first.md](./desktop-splash-first.md) | 桌面壳 splash 先行：保住「数据库失败先抛错」契约的正确启动顺序；**跑启动测试前先确认 5000 端口空闲**（端口被占会走「已有实例」分支，报成假回归） |
| [lazy-import-module-seam.md](./lazy-import-module-seam.md) | 惰性导入重依赖时保留测试 seam 的写法（`None` 声明 vs `__getattr__`）与「单独跑」验收 |
| [windows-chinese-path-exe-smoke.md](./windows-chinese-path-exe-smoke.md) | 中文路径下的 Windows 构建与隔离冒烟注意事项 |
| [green-folder-upgrade-and-data.md](./green-folder-upgrade-and-data.md) | 当前数据根、旧绿色目录及安全升级 |
| [portable-demo-package.md](./portable-demo-package.md) | 绿色版自带 200 条演示数据的五步打包流程、顺序硬约束、ZIP/安装包不得含演示数据、验收与两个坑（验收动作会弄脏包、`.bat` 行尾） |
| [versioning-github-backup.md](./versioning-github-backup.md) | 版本、备份、推送、打包和 GitHub Release 的分离核验 |
| [pywebview-save-print.md](./pywebview-save-print.md) | 桌面端异步保存后打开 PDF 的已落地模式 |

## 历史经验（阅读时不得当作当前缺陷/当前功能）

| Reference | 历史边界 |
|---|---|
| [desktop-print-session.md](./desktop-print-session.md) | 旧登录会话导致外部浏览器体验的历史问题；当前永久免登录，仍需区分壳内与外部浏览器 |
| [pywebview-save-print-405.md](./pywebview-save-print-405.md) | 旧 WebView 405 调查步骤，先复现请求再判断是否仍适用 |
| [erp-session-notes.md](./erp-session-notes.md) | 已脱敏的历史流程与 UX 经验；不保存公司/客户真实联系信息 |
| [_archived-full-body-20260725.md](_archived-full-body-20260725.md) | 旧版完整技能归档；不作为当前规则来源，凭据与个人信息不得恢复 |

更新功能后，同时检查 `docs/需求/` 和本目录对应专题。不要为了更新目录而把每份历史会话复盘改写成当前事实。
