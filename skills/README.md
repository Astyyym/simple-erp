# 简单ERP 项目技能索引

本项目有两个技能，职责不同，不要互相替代，也不要把它们合并成一个更大的技能。

| 技能 | 管什么 | 入口 |
|---|---|---|
| `simple-business-erp-development` | 项目总纲：业务口径、数据模型、开发与部署、打包发版、Windows 排障 | `simple-business-erp-development/SKILL.md` → `references/README.md` |
| `erp-ui-extension` | 在**已验收**的 UI 上加流程或改交互：控件、版式、主题、状态一致性、验收证据 | `erp-ui-extension/SKILL.md` → `references/README.md` |

## 进入项目后的读取顺序

1. 先读 `simple-business-erp-development/SKILL.md`（项目总纲，必读）。
2. 涉及界面、控件、版式、主题、交互的改动，再读 `erp-ui-extension/SKILL.md`。
3. 具体专题按各自 `references/README.md` 定位，**只读命中的那一份**。

## 边界

- 这两个技能只服务本项目，**不复制回全局技能目录**（DSH 全局技能根 `~/.dsh/skills`）；旧 Hermes 目录 `%LOCALAPPDATA%\hermes\skills` 是历史遗留，不往里写，也不要当成当前会话可用（本会话技能清单里没有 `product-ui` / `product-acceptance` 这些名字）。
- 跨项目可复用的方法论放全局技能（当前实际可用的是 `agent-development-discipline`）；本项目技能只放本项目的具体规则和实测结论。
- 新规则写进对应专题的 `references/`，不要堆回 `SKILL.md` 正文。

## 编写约定（避免「读不动」）

- **单行不超过约 1200 字符**：`read` 工具对超长单行会在 2000 字符处截断，一条规则写成 3000+ 字符的单行读不全，等于没写。长段落按句号 / 分号换行。
- **禁止 CR-only 行尾**：只用 `\r` 换行的文件在 PowerShell / 部分工具里会被当成一整行，长度统计和 diff 全错。统一 UTF-8 + LF（git 提交时按 autocrlf 处理）。
- **一个专题一份 reference**：变大就拆新文件并在 `references/README.md` 加一行，不要三份文件各写一段同一规则。
- **同一规则只留一处**：`memory/pitfalls.md` 只记「踩坑时间线」，可复用的规程与判据放 `references/`；重复处在 pitfalls 留一句指针即可。
