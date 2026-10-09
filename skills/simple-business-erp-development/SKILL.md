---
name: simple-business-erp-development
description: Use when developing or verifying the local simple ERP project in this directory.
version: 1.2.0
author: Project
license: MIT
metadata:
  hermes:
    tags: [simple-erp, flask, sqlite, windows, project]
    related_skills: [product-acceptance, repository-engineering, xlsx, pdf]
---

# 简单 ERP 项目技能

## Scope and source of truth

仅用于 `本地 Windows 仓库根目录`。这是 Windows 项目的唯一权威源码位置；WSL 只能访问同一目录作可选交叉测试，不维护第二份长期源码树。

开始任务前先读根目录 `AGENTS.md` 和 `docs/需求/消防器材店ERP系统-需求文档.md`。当前功能事实优先看源码、测试和 `VERSION`；专题决策与历史计划不得覆盖当前实现。文档分工见 `docs/README.md`，本技能 references 的职责和新旧边界见 `references/README.md`。

## Current product boundaries

- 技术边界：Flask + SQLite，可选 Windows PyWebView 桌面壳。
- 当前产品永久免登录，默认仅本机 `127.0.0.1` 使用；不能暴露局域网/公网，也不能把“免登录”说成有权限保护。
- 单号为 `MD` + `YYYYMMDD` + 四位流水；销售/退货共用当天流水，新建日期可改，重编辑保号保日期。
- 销售/退货单、客户货款汇总 PDF 与设置页样张默认 A4 竖版（210×297mm）；销售/退货内容正向适配，旧 241×140mm 版心已废止；打印机实打需另行验收。
- 源码运行数据默认在项目运行目录；冻结桌面版默认数据根为 Documents 下的 `简单ERP数据`；路径记忆只保存数据根位置。
- 不提交真实订单库、日志、密钥或个人联系信息。业务数据、日志、备份及构建文件不得纳入交付包，除非用户明确要求且已安全审查。
- 开单、金额、账款、导入、打印和数据目录规则以当前需求基线、对应 references 与现有测试为准，不凭记忆重写。

## Development and delivery

1. 检查当前 Git 分支、工作区和项目现有修改；保护用户未提交的文件与业务数据。
2. 明确本次目标、允许修改文件、非目标和验收方式；需求变化时先更新需求基线与短计划。
3. 写计划或候选功能条目时，对每条过「三问」（数据真的存在？用户真的需要？现有机制已解决？），依据见 [`references/plan-item-admission-three-questions.md`](references/plan-item-admission-three-questions.md)；判否的条目删除后必须留痕与理由，存疑的降为「待确认」并在开工前问用户。
4. UI/用户流程变化先遵循项目开发全流程技能的原型与验收要求；不因文档任务强行增加代码或原型。
5. 用隔离的临时 SQLite 数据库运行相关测试；优先使用 Windows 项目环境：`.venv\\Scripts\\python.exe -m pytest tests\\ -q`。
6. 需要时做 Windows 页面/桌面 EXE/打印机等对应层级的真实验收；测试通过不代替用户路径验证。
7. 分别报告 `passed`、`failed`、`blocked`、`unverified`；代码修改、测试、用户验收、打包、Git 推送和 Release 各自记录，不能互相推断。

## Planning and task-record lifecycle

- 新建开发短计划前先查 `开发短计划/README.md` 和相关现有计划。同一交付目标与验收范围应更新/复用既有计划；只有目标或验收边界确实不同，或是明确的后续阶段，才另开文件，并链接相关计划。短计划是一次交付的记录，不是长期需求文档。
- 根目录 `task_plan.md` 仅用于需要跨会话/多阶段跟踪的当前任务；同一仓库最多保留一个活动 `task_plan.md`，普通短任务不创建它，也不复制一份短计划。`findings.md` 仅记该活动任务的调查证据/决策，`progress.md` 仅记状态、阻塞和下一步；三者不得跨无关任务累积或重复存同一信息。
- 任务关闭时，把长期有效的产品规则、实现事实或验收标准迁入对应 `docs/` 或项目 reference；更新短计划为历史状态并更新索引，然后停止维护该任务的临时跟踪记录。清空、归档、移动或覆盖已有记录前，先检查 Git 状态和完整内容；不得擅自丢弃既有未提交或未跟踪资料。
- 仅为当前任务记录必要证据；不要把私人路径、真实业务数据、凭据或一次性操作细节写入长期需求和通用 reference。

## References

先读 [`references/README.md`](references/README.md) 选择当前规则或历史案例。详细打印、导入、数据目录、升级、打包与排障资料按需加载，不要把归档内容当现行产品状态。
