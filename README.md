# 消防ERP

简单ERP本地单机 ERP。

## 当前版本

v0.1.0

## 主要功能

- 销售开单
- 商品管理
- 客户管理
- 客户货款汇总表
- 客户价/常规价
- 回收站
- Windows 桌面版 EXE 打包

## 开发运行

```bash
cd '/mnt/d/wenjian/Hermes/ERP系统'
source .venv/bin/activate
PYTHONPATH=app python app/app.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

## Windows 桌面版打包

在 Windows 环境运行：

```bat
打包Windows桌面版.bat
```

产物：

```text
dist\消防ERP\消防ERP.exe
dist\消防ERP-Windows桌面版.zip
```

## 数据位置

正式数据位于运行目录：

```text
data\erp.db
```

升级程序时不要覆盖正式电脑上的 `data\erp.db`。
