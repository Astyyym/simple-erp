# 简单ERP

一个面向本地单机使用的小型进销存/开单系统，当前重点覆盖消防器材门店的日常销售开单、退货、客户账款、商品与客户资料管理、打印和本地数据保存。

## 当前版本

```text
v0.2.1
```

## 当前功能

### 开单与打印

- 新建销售单。
- 新建退货单。
- 销售单、退货单使用一致的录入界面。
- 支持按行录入商品名称、单位、数量、单价和备注。
- 支持保存后直接打开打印 PDF。
- 单据列表中退货单会用红色行标注，销售单保持普通样式。

### 商品与客户资料

- 商品管理：新增、编辑、删除、批量删除。
- 客户管理：新增、编辑、删除、批量删除。
- 商品和客户支持相似字符查询。
- 商品和客户查询栏支持一键清除信息。
- 开单时输入新商品可沉淀到商品字典。
- 支持客户价/常规价：同一商品可记录客户专属价格。

### 账款管理

- 按客户查询订单。
- 支持开始日期、结束日期筛选。
- 支持清除筛选条件。
- 查询结果中退货单用红色行标注。
- 客户余额按以下逻辑计算：

```text
期初余额 + 销售单金额 - 退货单金额 + 账务调整 - 已收款
```

### 客户货款汇总表

- 支持按客户和日期范围生成 PDF 汇总表。
- 汇总表按日期、单号、商品明细展开。
- 相同日期、相同单号会合并单元格。
- 同一单号的总金额只显示一次，避免每个商品行重复显示。
- 退货单明细在打印布局中标红。
- 底部显示销售与退货的计算式：

```text
销售总金额 - 退货总金额 = 总金额
```

### 回收站与数据安全

- 单据、商品、客户采用软删除。
- 删除后进入回收站，避免误删后立即丢失。
- SQLite 使用 WAL 模式。
- 启动时执行数据库完整性检查。
- 运行数据、日志、临时 PDF、备份目录与源码分离，避免误提交业务数据。

### Windows 桌面版

- 项目支持打包为 Windows 桌面版 EXE。
- 桌面版目标使用方式是：复制程序文件夹，双击 `消防ERP.exe` 使用。
- 正式数据保存在程序目录下的 `data/erp.db`。

## 仓库架构

```text
.
├── app/
│   ├── app.py                    # Flask 开发入口
│   └── erp/
│       ├── __init__.py            # create_app 应用工厂，注册蓝图和健康检查
│       ├── config.py              # 配置读取、运行目录和打包路径处理
│       ├── db.py                  # SQLite schema、迁移补列、完整性检查
│       ├── routes/                # 页面路由
│       │   ├── accounts.py        # 账款、汇总表、收款、调整
│       │   ├── customers.py       # 客户管理与客户名称提示
│       │   ├── orders.py          # 销售单、退货单、单据查询、打印入口
│       │   ├── products.py        # 商品管理与商品名称提示
│       │   └── recycle.py         # 回收站
│       ├── services/
│       │   └── accounting.py      # 业务记账、开单、客户余额、客户价
│       ├── templates/             # Jinja2 页面和打印模板
│       │   ├── accounts/
│       │   ├── customers/
│       │   ├── orders/
│       │   ├── products/
│       │   └── recycle/
│       └── utils/
│           ├── audit.py           # 审计日志
│           ├── backup.py          # 数据备份
│           ├── logging.py         # 日志配置
│           ├── money.py           # 元/分转换与金额计算
│           └── pdf.py             # 订单和账款汇总 PDF 生成
├── tests/                         # pytest 回归测试
├── docs/                          # 开发与 Windows 打包说明
├── desktop_app.py                 # Windows 桌面版启动入口
├── config.json                    # 本地配置
├── requirements.txt               # Python 依赖
├── VERSION                        # 当前版本号
├── setup.bat                      # Windows 环境准备脚本
├── dev_start.bat                  # Windows 开发启动脚本
├── 启动系统.bat / 启动系统.vbs     # Windows 日常启动入口
├── 停止系统.bat                   # Windows 停止入口
└── 打包Windows桌面版.bat          # Windows EXE 打包脚本
```

## 本地开发运行

### WSL / Linux 开发环境

```bash
cd '/mnt/d/wenjian/Hermes/ERP系统'
source .venv/bin/activate
PYTHONPATH=app python app/app.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

健康检查：

```text
http://127.0.0.1:5000/health
```

### 运行测试

```bash
cd '/mnt/d/wenjian/Hermes/ERP系统'
source .venv/bin/activate
PYTHONPATH=app pytest tests/ -q
```

## Windows 日常使用

开发阶段可在 Windows 里使用启动脚本：

```text
启动系统.vbs
```

或：

```text
启动系统.bat
```

停止服务：

```text
停止系统.bat
```

## Windows 桌面版打包

在 Windows 环境运行：

```bat
打包Windows桌面版.bat
```

生成产物：

```text
dist\消防ERP\消防ERP.exe
dist\消防ERP-Windows桌面版.zip
```

详细说明见：

```text
docs\Windows桌面版打包说明.md
```

## 数据位置与升级注意

开发运行时，业务数据位于：

```text
data\erp.db
```

Windows 桌面版运行时，业务数据位于 EXE 所在目录下：

```text
data\erp.db
```

升级程序时不要覆盖正式电脑上的 `data\erp.db`。建议至少备份整个 `data` 文件夹。

## 不提交到 Git 的运行数据

以下目录是运行期数据或构建产物，不应提交到仓库：

```text
data/
logs/
temp_pdf/
backups/
imports/
dist/
build/
.venv/
.venv-win/
```
