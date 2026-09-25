# Windows 桌面版打包说明

目标：生成用户可直接双击运行的 Windows 桌面版，不要求日常使用者打开终端、安装 Python 或连接互联网。

## 1. Windows 打包环境

- 当前默认业务环境为 Windows 11。构建脚本会创建 `.venv-win`，优先使用 `py -V:Astral/CPython3.11.15`，失败时尝试 `py -3.14`；不要用 WSL Python 打 Windows EXE。

优先直接运行 `打包Windows桌面版.bat`：脚本会在 `.venv-win` 不存在时创建环境（先尝试 `py -V:Astral/CPython3.11.15`，再回退 `py -3.14`），并安装/检查依赖。已有 `.venv-win` 时不要重复创建。只有手动准备独立的 Python 3.11 环境时才使用下列命令：

```bat
py -3.11 -m venv .venv-win
.venv-win\Scripts\python.exe -m pip install --upgrade pip
.venv-win\Scripts\python.exe -m pip install -r requirements.txt
.venv-win\Scripts\python.exe -m pip install pyinstaller pywebview
```

## 2. 打包

```bat
打包Windows桌面版.bat
```

当前 PyInstaller one-folder 产物目录：

```text
dist\简单ERP\
  简单ERP.exe
  config.json
  _internal\
```

打包脚本也可能生成版本化 ZIP。实际文件名及版本以本次打包脚本输出为准，常见示例：

```text
dist\简单ERP-Windows桌面版-vX.Y.Z.zip
dist\simple-erp-windows-vX.Y.Z.zip
```

仅本地存在 ZIP/EXE 不能证明其已上传 GitHub Releases；推送源码、构建包和发布 Release 是不同状态。

## 3. 安装与启动

将整个 `dist\简单ERP` 文件夹复制到目标 Windows 电脑，例如 `D:\简单ERP\`，然后创建 `简单ERP.exe` 的快捷方式。不要只复制 EXE，one-folder 依赖 `_internal`。

程序运行本地服务，桌面窗口访问 `127.0.0.1:5000`。当前版本永久免登录，任何能访问该端口的人都可能操作数据；不得把服务开放给局域网或公网。

## 4. 数据目录

| 项 | 当前行为 |
|---|---|
| 新装桌面版默认数据根 | `%USERPROFILE%\Documents\简单ERP数据` |
| 业务数据库 | `{数据根}\data\erp.db` |
| 路径记忆 | `%LOCALAPPDATA%\简单ERP\data_location.json`，只记录数据根路径 |
| 设置 | 设置页显示当前/默认路径；桌面版可选文件夹并迁移数据 |
| 源码运行 | 默认数据根为项目运行目录，通常是仓库根目录 |

数据根可以由用户在设置中更改。迁移前备份；目标目录已有非空数据库时程序会拒绝覆盖；迁移后旧目录仍保留，不要未经确认删除。

## 5. 备份与升级

升级前至少备份设置页显示的数据根目录下的 `data` 文件夹。退出程序后，仅替换程序文件（EXE、`_internal` 等）；不要用构建包中的空库覆盖用户现有的 `data\erp.db`。

如果旧安装把数据放在 EXE 旁边的 `data\`，整目录覆盖风险更高；应先确认实际数据根目录。无需通过卸载程序完成升级。

## 6. 打印

- 销售单、退货单、客户货款汇总 PDF 和设置页样张默认使用 A4 竖版（210×297mm）；销售/退货单内容为正向竖版排版，不再放入旧 241×140mm 单据区。
- 打印驱动建议选 A4 纵向、实际大小/100%，关闭“适合页面/缩放”。历史 `NantianPR-LQ` 记录曾提供 A4 纸型，但当前驱动版本和目标电脑选项未重新核验。
- 设置页的横纵偏移和缩放只校准销售/退货单及设置样张；账款汇总 PDF 不使用这些偏移。
- 先检查 HTML/PDF 预览，再在目标电脑/打印机实打校准。生成 PDF 或自动化测试通过不能替代目标打印机人工验收。

## 7. 证据与发布状态

每次正式交付分别记录：

1. 测试与源码运行结果；
2. Windows EXE 是否从隔离目录真实启动、`/health` 版本是否正确、业务页是否可用；
3. ZIP 是否正确生成及其文件名/大小；
4. Git 是否提交/推送；
5. GitHub Release 是否存在且包含预期资产。

不得把其中一项推断成其他项已完成。打包流程和 Windows 中文路径冒烟细节见项目技能 references 索引。
