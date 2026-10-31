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

- 这两个技能只服务本项目，**不复制回 Hermes 全局技能目录**（`~/AppData/Local/hermes/skills`）。
- 全局技能里保留的是跨项目可复用的方法论：`product-ui`、`product-acceptance`、`project-development-delivery-discipline` 等；本项目技能里放的是本项目的具体规则和实测结论。
- 新规则写进对应专题的 `references/`，不要堆回 `SKILL.md` 正文。
