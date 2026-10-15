# 设置页与数据根目录（当前实现）

> 本文记录当前简单ERP目录与设置行为。早期版本的单账号登录门已在 v0.8.0 被永久免登录取代；不要从旧短计划复制旧账户凭据或配置。

## 数据根与路径记忆

| 概念 | 当前行为 |
|---|---|
| 源码运行默认数据根 | 项目运行目录，数据库通常为 `{项目根}\data\erp.db` |
| 冻结桌面版默认数据根 | `%USERPROFILE%\Documents\简单ERP数据` |
| 路径记忆文件 | `%LOCALAPPDATA%\简单ERP\data_location.json`，只保存当前数据根路径，不含数据库 |
| 桌面程序运行根 | `Path(sys.executable).parent` |
| 可写配置 | 运行目录旁 `config.json`；业务数据目录由数据根独立决定 |

`ERP_DATA_ROOT` 可用于显式指定数据根；路径记忆文件用于后续启动恢复选择。源码环境和冻结版默认值不同，不能把源码数据库位置写成桌面版默认值。

## 设置页能力

- 入口：侧栏「系统 → 设置」，路由 `/settings/`。
- 公司名称同步系统名称与 PDF 抬头。
- 主题：浅色/深色/跟随系统；界面缩放：100%/125%/150%，不改变打印 PDF 页面规格。
- 可配置打印文案，并在「打印版面预览」卡片里为**三个版面**各生成 HTML/PDF 样张：四类开单（241mm 连续纸）、客户货款汇总表、往来交易对账单（两张 A4 竖版）。样张全部是**固定示例数据**（`utils/pdf.py` 里的 `settings_*_preview_context`），不读业务库、不写真实单据；桌面壳里「生成预览 PDF」走统一预览页（可保存/打印/返回上一级），浏览器里直接返回真实 PDF。三个样张共用同一套「未保存修改」确认提示（`data-print-preview`）。
- 可设置打印横纵偏移及缩放；改偏移前先核对当前 A4 竖版 PDF 页框与预览。偏移仅作用于销售/退货单及设置样张，不作用于账款汇总表；旧 241×140mm 版心见历史 reference `sales-slip-paper-241x140.md`。
- 桌面壳提供原生选文件夹；浏览器预览可手动填写路径。
- 修改数据根会拷贝业务目录并写入路径记忆；目标已有非空 `erp.db` 时拒绝覆盖；旧目录保留。
- **清空所有数据**（`POST /settings/purge-all-data`，卡片 `#purgeSection`）：把系统恢复成「刚装好、还没录单」。清空四类单据与明细、客户/商品资料与客户价、库存状态/流水/期初、收付款与调整、对账快照、审计日志、单号序列表，以及 `product_images/`、`temp_pdf/`、`logs/` 的**内容**；并把公司名、打印信息、主题/缩放/字重、打印偏移回出厂。**保留** `backups/`、`legacy_archive/`、数据目录路径记忆与 `data/erp.db` 文件本身。安全：必须输入确认词「清空」，执行前自动 `create_backup("pre_purge")`，留痕写 `logs/purge.log`（审计表已被清空，故必须落文本）。**单号流水高位保留**：清空前把各业务日期的 MD/NH 最大流水写回 `order_number_sequences`/`purchase_number_sequences`，清空后同一天的新单续排高位，避免与已打出的纸质单撞号。清空实现集中在 `app/erp/utils/reset.py`。
- 桌面壳支持原生保存 PDF 对话框。

## 鉴权边界

当前产品永久免登录，`local_access_*` 配置字段属于兼容旧数据的历史键，不是当前可启用的登录设置。`/health` 公开返回 `auth: disabled`。默认只在本机使用，不暴露局域网或公网。

## 当前实现入口

- `app/erp/config.py`：运行根、数据根、路径记忆、配置读写与目录迁移。
- `app/erp/routes/settings.py`：设置保存、数据迁移、打印样张预览。
- `app/erp/templates/settings/index.html`：设置界面。
- `desktop_app.py`：桌面原生选择目录/保存 PDF 与 WebView 启动。
- 测试：`tests/test_settings.py`、`tests/test_auth_login.py`、`tests/test_desktop_pdf_save.py`。

历史的设置页/单账号计划见 `开发短计划/2026-07-12_设置页_需求与执行计划.md`；它只用于回顾 v0.5.0，不代表当前登录状态或发布状态。
