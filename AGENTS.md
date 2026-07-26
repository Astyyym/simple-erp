# AGENTS.md — 简单ERP

给 AI coding agent 的项目规矩。改代码前先读本文件。

## 项目是什么

本地单机进销存/开单（消防器材门店向）。Flask + SQLite + 可选 Windows 桌面壳。

- 正式目录（唯一真源）：`D:\wenjian\Hermes\简单ERP`
- GitHub：`Astyyym/simple-erp`，默认分支 `main`
- 当前版本见根目录 `VERSION`

## 怎么跑

- Windows 开发：双击或运行 `启动系统.bat` / `dev_start.bat`
- 桌面版：`dist\简单ERP\简单ERP.exe`（升级勿覆盖用户数据目录）
- 测试：在项目根用现有 venv 跑 `pytest`（以 `tests/` 为准）
- 健康检查：`/health`（无需登录，打包冒烟用）

## 可以改

- `app/` 业务与前端
- `tests/` 测试
- `docs/`、`开发短计划/` 说明与短计划
- `CHANGELOG.md`、`VERSION`、`README.md`（仅在哥哥确认功能/发版时）

## 不要动 / 不要做

- 不要把正式目录拷成第二套「真源」长期双开
- 不要擅自 `git push`、打 Release、覆盖用户机器上的业务数据
- 不要提交真实订单库、密钥、本机绝对路径进公开说明的不当位置
- 不要用 WSL 当权威源码树；交叉测试可以，真源在 Windows 目录
- UI：先复述确认再改。顶栏钉住 = content flex + page-body 滚动，保留毛玻璃；侧栏指定 HEX 时实色关 blur
- 开单：默认 1 行；关浏览器自动填充；一键清空恢复 1 行；客户价/常规价逻辑保持稳定

## 单据与打印（别改崩）

- 单号：`MD` + 8 位日期 + 4 位流水
- 实体纸约 241×140mm；正式 PDF 为 A4 横向，单据区在上方居中
- 打印相关改动必须说明如何冒烟（预览 PDF / 设置页偏移）

## 交付习惯

1. 先对齐要改什么、验收标准；大改先写/更新 `开发短计划/` 或本目录 `task_plan.md`
2. 改完：相关测试 + 必要的手测（开单/列表/打印/登录视范围而定）
3. 未过交付门：只说开发中，不宣称已发版
4. 长任务：维护 `task_plan.md` / `findings.md` / `progress.md`

## 文档放哪

| 类型 | 位置 |
|------|------|
| 需求/复盘 | `docs/` |
| 短执行计划 | `开发短计划/` |
| 本任务进度三件套 | 仓库根：`task_plan.md`、`findings.md`、`progress.md` |
