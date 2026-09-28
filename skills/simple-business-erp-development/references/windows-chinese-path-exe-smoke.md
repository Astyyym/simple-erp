# Windows EXE 冒烟测试（中文项目路径）

## 适用问题

项目目录或 EXE 名称含中文时，WSL→Windows 传递的命令行/PowerShell 脚本可能发生编码或转义问题。遇到路径“看起来不存在”时，先用 Windows Python 对用户给定的绝对路径核实，不要改写路径或删除目录。

## 当前项目构建边界

- 权威源码：`本地 Windows 仓库根目录`
- Windows `.venv-win` + PyInstaller one-folder；不得用 WSL Python 打 Windows EXE。
- 推荐入口：`打包Windows桌面版.bat`；非交互执行可用 Windows venv 的 PyInstaller 与 `简单ERP.spec`。
- 当前产品永久免登录；健康检查只检查服务状态/版本及 `auth: disabled`，不需要登录页或账户字段。

## 隔离冒烟原则

1. 确认目标版本来自 `VERSION`，并先确认 5000 端口占用者；不要根据端口号猜测服务来源。
2. 将整个 `dist\简单ERP` 复制到 ASCII-only 临时目录，例如 `%LOCALAPPDATA%\Temp\simple_erp_smoke\`，与正式安装/业务数据隔离。
3. 冒烟前使用临时 `ERP_DATA_ROOT`，不得连接真实业务数据库。
4. 从 Windows 侧启动隔离副本并轮询 `http://127.0.0.1:5000/health`，核对 `status=ok`、版本与 `auth: disabled`。
5. 验证 `/`、`/orders/new`、设置页以及本次改动涉及的真实用户路径；健康检查通过不足以代表页面功能通过。
6. 结束后只停止本次临时副本进程，并核对正式数据目录未被触碰。

## PowerShell/WSL 编码注意

- PowerShell 变量避免使用自动变量 `$PID`；可用 `$procId`。
- 从 Bash 调 Windows 可执行程序时，绝对路径使用 Windows 原生可识别的 `C:/...` 格式；PowerShell 脚本文件路径应加引号。
- 需要 PowerShell 的复杂逻辑时写入 ASCII-only 临时 `.ps1`，不要在跨 shell 的未引用命令中嵌入 `$变量`。
- Windows 用户侧结果优先从 Windows PowerShell/browser 获取，WSL 内部访问结果不代表 Windows localhost 实际命中的实例。

## 发布区分

本地 EXE 冒烟通过、Git 推送和 GitHub Release 上传是不同验收项。上传后必须重新查询对应 Release 和资产列表，不能从本地 ZIP 文件存在推断发布成功。
