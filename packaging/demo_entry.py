"""演示数据版桌面入口 —— 只用于「打开即用」的绿色文件夹版。

普通安装版 / 源码运行仍走 ``desktop_app.py``（冻结版数据根默认
「文档\\简单ERP数据」）。绿色文件夹版要在**文件夹里自带 200 条演示业务数据**，
因此本入口在导入 erp 任何模块之前，把数据根 / 配置 / 路径记忆**强制**指向
EXE 所在目录：

- 数据根：``<包目录>/demo_data``（erp.db、备份、日志、图片都在里面）
- 配置：``<包目录>/config.json``（打包脚本已把出厂配置复制到包根）
- 路径记忆：``<包目录>/demo_data/location.json``（不读机器级记忆）

为什么是强制赋值而不用 ``setdefault``：机器上可能已有指向正式数据的路径记忆
（``%LOCALAPPDATA%\\简单ERP\\data_location.json``），演示版必须无视它，
否则会把演示数据写进用户真实数据根、或让用户正式数据出现在演示包里。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    raise RuntimeError("demo_entry 只用于打包后的绿色文件夹版；源码运行请用 desktop_app.py")

_package = Path(sys.executable).resolve().parent
os.environ["ERP_DATA_ROOT"] = str(_package / "demo_data")
os.environ["ERP_CONFIG_PATH"] = str(_package / "config.json")
os.environ["ERP_LOCATION_FILE"] = str(_package / "demo_data" / "location.json")

import desktop_app  # noqa: E402  环境变量就绪之后才能导入 erp 相关模块

if __name__ == "__main__":
    desktop_app.main()
