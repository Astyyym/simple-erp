# 简单ERP

一个面向本地单机使用的小型进销存/开单系统，当前重点覆盖消防器材门店的日常销售开单、退货、客户账款、商品与客户资料管理、打印和本地数据保存。

## 当前版本

```text
v0.4.0
```

GitHub Releases：https://github.com/Astyyym/simple-erp/releases

## 当前功能

### 桌面界面与录入体验

- 企业 ERP 风格工作台、分组侧边栏、统一表格/表单样式
- 销售单/退货单支持“一键清空”，默认保留一行空白明细
- 关闭浏览器对开单字段的历史自动填充，保留 ERP 字典联想
- 账款页聚合查询客户余额

### 开单与打印

- 销售单、退货单共用录入界面
- 单号：`MD` + 八位日期 + 四位流水（如 `MD202607120001`）
- 销售/退货共用当天流水
- 新建日期默认当天可改，单号随日期重算；重编辑保号保日期
- 保存后可直接打开打印 PDF；退货单列表标红

### 单据管理

- 支持按年 / 按月到月（可跨年） / 按日到日
- 支持全部 / 只看销售 / 只看退货
- 客户采购统计：唯一客户出图、多客户提示、空客户全店
- 导出货款汇总表（PDF 预览，多客户分段）

### 商品与客户资料

- 增删改、模糊查询
- Excel/CSV 模板下载与批量导入（同名跳过）
- 客户价 / 常规价

### 账款管理

- 按客户与日期筛选订单
- 余额：期初 + 销售 - 退货 + 调整 - 收款
- 旧账款入口保留

### Windows 桌面版

- 复制 `dist\消防ERP` 文件夹，双击 `消防ERP.exe`
- 正式数据在程序目录 `data\erp.db`

## 仓库目录（整理后）

```text
.
├── README.md / CHANGELOG.md / VERSION
├── app/                       # 业务源码
├── tests/                     # pytest
├── docs/                      # 长期文档
│   ├── README.md              # 文档索引
│   ├── 开发说明.md
│   ├── Windows桌面版打包说明.md
│   ├── 需求/                  # 需求文档
│   └── 复盘/                  # 复盘与纪律
├── 开发短计划/                # 当次可执行短计划
├── desktop_app.py             # 桌面版入口
├── 消防ERP.spec               # PyInstaller 规格
├── 打包Windows桌面版.bat
├── 启动系统.bat / 启动系统.vbs / 停止系统.bat
├── 启动系统-WSL版.bat / 停止系统-WSL版.bat   # 仅开发机
├── setup.bat / dev_start.bat
├── config.json
└── requirements.txt
```

运行期/构建期目录（不提交）：

```text
data/  logs/  temp_pdf/  backups/  imports/
dist/  build/  .venv/  .venv-win/  __pycache__/
```

## 本地开发

### WSL

```bash
cd '/mnt/d/wenjian/Hermes/ERP系统'
source .venv/bin/activate
PYTHONPATH=app python app/app.py
```

源码预览建议 `5001`，避免与正式 EXE 的 `5000` 冲突。

### 测试

```bash
source .venv/bin/activate
PYTHONPATH=app pytest tests/ -q
```

## Windows 打包

```bat
打包Windows桌面版.bat
```

产物：

```text
dist\消防ERP\消防ERP.exe
dist\消防ERP-Windows桌面版.zip
dist\simple-erp-windows-vX.Y.Z.zip
```

说明见：`docs\Windows桌面版打包说明.md`

## 升级注意

替换程序文件时**不要覆盖**对方电脑上的 `data\erp.db`，先备份整个 `data` 文件夹。
