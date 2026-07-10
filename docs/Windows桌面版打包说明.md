# Windows 原生 EXE 打包说明

目标：生成父母电脑可直接双击使用的 Windows 桌面端程序，不依赖 WSL，不要求他们打开命令行。

## 1. 在 Windows 里准备打包环境

在项目目录 `D:\wenjian\Hermes\ERP系统` 打开 PowerShell 或 CMD：

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
dist\消防ERP\
  消防ERP.exe
  config.json
  data\
  logs\
  temp_pdf\
```

## 3. 给父母电脑安装

把整个目录复制到父母电脑，例如：

```text
D:\消防ERP\
```

然后给桌面创建 `消防ERP.exe` 的快捷方式。

日常使用：双击桌面图标即可。

## 4. 数据备份

正式数据主要在：

```text
D:\消防ERP\data\erp.db
```

备份时至少备份整个 `data` 文件夹。不要只复制 exe。
