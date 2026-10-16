# AGENTS.md — 简单ERP

给 AI coding agent 的项目规矩。改代码前先读本文件及适用的项目技能。

## 项目是什么

本地单机销售/退货开单、客户商品资料与账款系统（消防器材门店向）。Flask + SQLite，可选 Windows PyWebView 桌面壳。

- 正式目录（唯一真源）：本地 Windows 项目根目录
- GitHub：`Astyyym/simple-erp`，默认分支 `main`
- 当前源码版本以根目录 `VERSION` 为准；当前工作区 `VERSION` 为 `v3.15.0`
- 当前需求权威文档：`docs/需求/消防器材店ERP系统-需求文档.md`

## 怎么跑

- Windows 源码开发：首次准备运行 `setup.bat`（创建 `.venv` 并装依赖），随后用 `.venv\Scripts\python.exe app\app.py` 起服务
- Windows 桌面版（免安装）：`dist\local-vX.Y.Z\简单ERP\简单ERP.exe`
- Windows 安装包（推荐给门店）：`打包Windows安装包.bat` → `dist\simple-erp-setup-vX.Y.Z.exe`，per-user 装到 `%LOCALAPPDATA%\Programs\简单ERP`
- 测试：`.venv\Scripts\python.exe -m pytest tests\ -q`；打包 venv 可用 `.venv-win\Scripts\python.exe -m pytest tests\ -q`
- 健康检查：`http://127.0.0.1:5000/health`（公开端点，返回 `auth: disabled`）
- WSL：只通过 Windows 盘映射访问同一仓库做可选交叉测试；不建立第二套长期源码。

## 交付产物（三种形态，缺一不可）

每次打包/发版都出齐这三种，都由 `打包Windows安装包.bat` 一次生成：

| 形态 | 产物 | 用途 |
|---|---|---|
| 绿色版目录 | `dist\local-vX.Y.Z\简单ERP\` | 本机/U 盘直接跑；**自带 200 条演示数据**（打开即用） |
| **安装包** | `dist\simple-erp-setup-vX.Y.Z.exe` | 门店用户安装（开始菜单/卸载项） |
| 交付 ZIP | `dist\simple-erp-windows-vX.Y.Z.zip` | 发给别人（解压即用） |

- 安装包与 ZIP 是**同一份程序**（正常入口 `简单ERP.spec`，从 `build\clean-vX.Y.Z` 构建），只差投递方式；安装包是 Inno Setup 包在 one-folder 产物之上。
- **绿色版是另一份构建**（用户 2026-10-14 决策）：用 `简单ERP-demo.spec`，入口 `packaging/demo_entry.py` 把数据根强制指向包内 `demo_data\`，并在打包最后一步由 `packaging/seed_demo_data.py` 写入 200 条演示数据。**安装包与 ZIP 不得含演示数据**（`make_release_zip.py` 会在写盘前拒绝 `demo_data`）。构建顺序固定为：干净产物 → 安装包 → ZIP → 绿色版 → 写演示数据。
- 安装包**必须 per-user**（`{localappdata}\Programs\简单ERP`），不得改成 Program Files：程序把可写 `config.json` 写在 EXE 旁，装进受保护目录会让保存设置失败。契约见 `tests/test_installer_packaging.py`。
- 只保留当前版本的产物；新版本打包成功后删除旧的 `local-v*` 目录与旧 ZIP，避免用户点错同名 `简单ERP.exe`。

## 可以改

- `app/` 业务与前端；`tests/` 测试
- `docs/`、`开发短计划/`、本项目 `skills/simple-business-erp-development/`
- `CHANGELOG.md`、`VERSION`、`README.md`：仅在需求和发版范围明确时更新

## 不要动 / 不要做

- 不要将正式目录复制成第二套长期“真源”；不要将 WSL 当权威源码树。
- 不要擅自 `git push`、打 Release、覆盖用户机器上的业务数据。
- 不要把真实订单库、日志、密钥、个人联系信息或不必要本机绝对路径写入公开说明。
- 不要把这些字段与表写回代码（2026-10-16 用户决策，schema v15 已物理删除）：`products.default_price_cents`、`products.safety_stock_3dp`、`order_items` 的成本三列（`unit_cost_micro` / `cost_total_micro` / `cost_source`）、`purchase_return_items` 的成本两列与 `posting_seq`、`purchase_return_orders.posted_once`，以及 `inventory_postings` / `inventory_initializations` / `product_inventory_state` 三张表。售价唯一来源 = 开单学习出的客户专属价（无专属价时单价留空手输，手输 > 0 才学）。改数据库前先读 `docs/需求/` 与本次迁移记录；老库升级不可逆，回退只能靠迁移前自动生成的整库备份。
- 当前产品永久免登录，默认仅本机 `127.0.0.1` 使用；不得把服务暴露到局域网/公网，也不要擅自加回登录门。
- UI：先对齐目标和验收再改。顶栏钉住 = content flex + page-body 滚动，保留毛玻璃；侧栏指定 HEX 时用实色并关闭 blur。
- 开单：默认 1 行；关闭浏览器历史自动填充；一键清空恢复 1 行；单价取该客户专属价（无专属价则留空手输，手输 > 0 才学成专属价）的逻辑保持稳定。

## 单据与打印（别改崩）

- 单号：`MD` + 8 位日期 + 4 位流水；销售与退货共用当天流水；新建日期可改，重编辑保号保日期。
- 拿货/退拿货共用 `NH` + 8 位日期 + 4 位流水（同一日号池）；取号需让过历史 `TN` 退拿货流水，旧单号不重写。取号实现集中在 `app/erp/utils/order_numbering.py`。
- 四类开单（销售/退货/拿货/退拿货）PDF 为 **241mm 连续纸一联**（逻辑页 215.9×139.7mm、页内左右各 7.3mm 版心，单联**默认 10 行**，设置页可选 6/8/10/12）；客户货款汇总、往来对账单 PDF 为 **A4 竖版（210×297mm）**。两套纸型不得混用。（2026-10-14 起「账款流水 PDF」已删除，内容并入两段式对账单。）
- 打印相关改动必须说明 PDF 预览、页面尺寸和设置页偏移的验证方式；自动测试不代替目标打印机实打。

## 交付习惯

1. 先对齐改动范围与验收标准；大改先写/更新 `开发短计划/` 或根目录 `task_plan.md`。
2. 改完运行相关测试，并按范围做真实页面/桌面/打印验收；测试不得污染正式 `data/erp.db`。
3. 未过交付门，只能说明已实现/已测试到哪一层，不宣称已发版。
4. 长任务按需维护根目录 `task_plan.md`、`findings.md`、`progress.md`。
5. `git push`、打包与 GitHub Release 分开核实和报告；不根据本地文件推断远端发布状态。
6. `build\` 下的构建树由脚本回收，不手工清理：每个版本会在 `build\` 留下 `clean-<版本>` / `cleanwork-<版本>` / `local-<版本>` 三份（约 193 MB），`打包Windows安装包.bat` 在开工前会删掉所有**非当前版本**的三类目录，只保留与 `VERSION` 完全同名的那一份（当前版本那三份留到下一版本打包时回收）。旧版本构建树不被任何脚本引用，所以不要用通配符手工删 `build\`，也不要为单个任务另建私有构建目录。`dist\` 里的旧产物（旧 `local-*` 目录、旧 ZIP/安装包）仍按「交付产物」一节的规矩由人工清理。

## 文档放哪

| 类型 | 位置 |
|---|---|
| 当前产品需求/实现事实 | `docs/需求/消防器材店ERP系统-需求文档.md` |
| 专题需求/已确认决策 | `docs/需求/` |
| 旧需求原文归档 | `docs/归档/需求/` |
| 使用、开发、打包说明 | `docs/`（入口为 `docs/README.md`） |
| 复盘 | `docs/复盘/` |
| 单次执行短计划 | `开发短计划/`（含历史状态索引；**本地私有，不随公开仓库发布**，正文以路径引用而非链接） |
| 本任务长进度 | 根目录 `task_plan.md`、`findings.md`、`progress.md`；仅供一个活动的多阶段任务使用 |
| 项目专属长期记忆 | `memory/`（入口 `memory/README.md`） |

短计划和根目录任务记录的创建、复用、关闭与保留规则，遵循项目技能 `Planning and task-record lifecycle`；不要把历史短计划当作当前待办，也不要清理未经检查的既有未提交/未跟踪记录。

## 项目记忆

进入本项目后先读 `memory/README.md`（项目专属长期记忆：业务真实场景、环境边界、已踩过的坑），再按索引按需读取。

- 新增/更新项目记忆写进 `memory/`，不要写回 Hermes 全局记忆；全局记忆不重复本项目细节。
- 分类边界见 `memory/README.md`：可复用规则进 `skills/.../references/`，产品规格进 `docs/需求/`，一次性过程进 `开发短计划/`。
- 硬规则仍留在本文件正文——`memory/` 不会被自动加载，只放「每次进项目都该知道的长期事实」。

## 项目技能路由

进入本项目后，涉及开发、业务规则或交付验收时读取：

- 项目主技能：`skills/simple-business-erp-development/SKILL.md`
- 参考索引：`skills/simple-business-erp-development/references/README.md`
- 打印、导入、数据目录、打包等细节按索引按需读取对应 `references/`

项目专用技能只服务本项目，不加入 Hermes 全局技能目录。
