# AGENTS.md — 简单ERP

给 AI coding agent 的项目规矩。改代码前先读本文件及适用的项目技能。

## 项目是什么

本地单机销售/退货开单、客户商品资料与账款系统（消防器材门店向）。Flask + SQLite，可选 Windows PyWebView 桌面壳。

- 正式目录（唯一真源）：本地 Windows 项目根目录
- GitHub：`Astyyym/simple-erp`，默认分支 `main`
- 当前源码版本以根目录 `VERSION` 为准；当前工作区 `VERSION` 为 `v3.2.0`
- 当前需求权威文档：`docs/需求/消防器材店ERP系统-需求文档.md`

## 怎么跑

- Windows 源码开发：首次准备运行 `setup.bat`，启动使用 `启动系统.bat` / `启动系统.vbs`
- Windows 桌面版：`dist\简单ERP\简单ERP.exe`
- 测试：`.venv\Scripts\python.exe -m pytest tests\ -q`；打包 venv 可用 `.venv-win\Scripts\python.exe -m pytest tests\ -q`
- 健康检查：`http://127.0.0.1:5000/health`（公开端点，返回 `auth: disabled`）
- WSL：只通过 Windows 盘映射访问同一仓库做可选交叉测试；不建立第二套长期源码。

## 可以改

- `app/` 业务与前端；`tests/` 测试
- `docs/`、`开发短计划/`、本项目 `skills/simple-business-erp-development/`
- `CHANGELOG.md`、`VERSION`、`README.md`：仅在需求和发版范围明确时更新

## 不要动 / 不要做

- 不要将正式目录复制成第二套长期“真源”；不要将 WSL 当权威源码树。
- 不要擅自 `git push`、打 Release、覆盖用户机器上的业务数据。
- 不要把真实订单库、日志、密钥、个人联系信息或不必要本机绝对路径写入公开说明。
- 当前产品永久免登录，默认仅本机 `127.0.0.1` 使用；不得把服务暴露到局域网/公网，也不要擅自加回登录门。
- UI：先对齐目标和验收再改。顶栏钉住 = content flex + page-body 滚动，保留毛玻璃；侧栏指定 HEX 时用实色并关闭 blur。
- 开单：默认 1 行；关闭浏览器历史自动填充；一键清空恢复 1 行；客户价/常规价逻辑保持稳定。

## 单据与打印（别改崩）

- 单号：`MD` + 8 位日期 + 4 位流水；销售与退货共用当天流水；新建日期可改，重编辑保号保日期。
- 拿货/退拿货共用 `NH` + 8 位日期 + 4 位流水（同一日号池）；取号需让过历史 `TN` 退拿货流水，旧单号不重写。取号实现集中在 `app/erp/utils/order_numbering.py`。
- 销售/退货、汇总及设置样张 PDF 为 A4 竖版（210×297mm）；旧 241×140mm PDF 版心已废止。
- 打印相关改动必须说明 PDF 预览、页面尺寸和设置页偏移的验证方式；自动测试不代替目标打印机实打。

## 交付习惯

1. 先对齐改动范围与验收标准；大改先写/更新 `开发短计划/` 或根目录 `task_plan.md`。
2. 改完运行相关测试，并按范围做真实页面/桌面/打印验收；测试不得污染正式 `data/erp.db`。
3. 未过交付门，只能说明已实现/已测试到哪一层，不宣称已发版。
4. 长任务按需维护根目录 `task_plan.md`、`findings.md`、`progress.md`。
5. `git push`、打包与 GitHub Release 分开核实和报告；不根据本地文件推断远端发布状态。

## 文档放哪

| 类型 | 位置 |
|---|---|
| 当前产品需求/实现事实 | `docs/需求/消防器材店ERP系统-需求文档.md` |
| 专题需求/已确认决策 | `docs/需求/` |
| 旧需求原文归档 | `docs/归档/需求/` |
| 使用、开发、打包说明 | `docs/`（入口为 `docs/README.md`） |
| 复盘 | `docs/复盘/` |
| 单次执行短计划 | `开发短计划/`（含历史状态索引） |
| 本任务长进度 | 根目录 `task_plan.md`、`findings.md`、`progress.md`；仅供一个活动的多阶段任务使用 |

短计划和根目录任务记录的创建、复用、关闭与保留规则，遵循项目技能 `Planning and task-record lifecycle`；不要把历史短计划当作当前待办，也不要清理未经检查的既有未提交/未跟踪记录。

## 项目技能路由

进入本项目后，涉及开发、业务规则或交付验收时读取：

- 项目主技能：`skills/simple-business-erp-development/SKILL.md`
- 参考索引：`skills/simple-business-erp-development/references/README.md`
- 打印、导入、数据目录、打包等细节按索引按需读取对应 `references/`

项目专用技能只服务本项目，不加入 Hermes 全局技能目录。
