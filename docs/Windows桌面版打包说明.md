# Windows 原生 EXE 打包说明

目标：生成父母电脑可直接双击使用的 Windows 桌面端程序，不依赖 WSL，不要求他们打开命令行。

## 1. 在 Windows 里准备打包环境

在唯一正式项目目录 `D:\wenjian\Hermes\简单ERP` 打开 PowerShell 或 CMD：

```bat
py -3.11 -m venv .venv-win
.venv-win\Scripts\python.exe -m pip install --upgrade pip
.venv-win\Scripts\pip.exe install -r requirements.txt
.venv-win\Scripts\pip.exe install pyinstaller pywebview
```

> 不要用 WSL 的 `.venv` 打 Windows exe。必须用 Windows Python 的 venv。

## 2. 打包

```bat
打包Windows桌面版.bat
```

生成目录：

```text
dist\简单ERP\
  简单ERP.exe
  config.json
  _internal\   （依赖；以实际打包结果为准）
```

本地还会同步生成 ZIP（示例）：

```text
dist\简单ERP-Windows桌面版.zip
dist\简单ERP-Windows桌面版-vX.Y.Z.zip
dist\simple-erp-windows-vX.Y.Z.zip
```

GitHub Releases 优先上传英文文件名 ZIP（中文文件名在上传时可能乱码）。

## 3. 给父母电脑安装

把整个目录复制到父母电脑，例如：

```text
D:\简单ERP\
```

然后给桌面创建 `简单ERP.exe` 的快捷方式。

日常使用：双击桌面图标即可。

## 4. 数据目录（与程序分离）

| 项 | 说明 |
|----|------|
| 默认（新装 EXE） | `文档\简单ERP数据`（`%USERPROFILE%\Documents\简单ERP数据`） |
| 业务库 | `{数据根}\data\erp.db` |
| 路径记忆 | `%LOCALAPPDATA%\简单ERP\data_location.json`（只存路径） |
| 设置 | 侧栏「设置」可查看当前目录；「浏览文件夹」+ 一键迁移 |

开发源码默认仍用**项目根**下 `data\erp.db`，与 EXE 默认 Documents 不同。

## 5. 数据备份

备份时至少备份整个**当前数据根**下的 `data` 文件夹（设置页可看完整路径）。
不要只复制 exe。

## 6. 升级

1. 退出程序
2. 备份数据目录
3. 覆盖程序文件（exe / `_internal` 等）
4. **不要**覆盖正在使用的 `data\erp.db`
5. 若用户曾把数据目录选在程序夹旁（旧绿色版习惯），覆盖时尤其不要抹掉该夹下 `data\`

## 7. 设置页（桌面）

- 公司名称、主题、缩放、打印文案：改完点「保存设置」
- 未保存就点侧栏其它页：会询问是否保存
- 数据目录：点「浏览文件夹…」打开系统选夹，再「一键迁移并切换」
- 窗口标题会随公司名称变化
