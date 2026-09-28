# Agent-managed development preview

适用于需要在 Windows 浏览器验收源码页面的 ERP 迭代。只有预览服务真实启动并确认用户访问的是正确实例后，才提供预览链接。

## 端口与启动者

| 场景 | 地址/端口 | 启动者 |
|---|---|---|
| 桌面版或源码默认入口 | `127.0.0.1:5000` | 用户/启动脚本；勿与验收预览混淆 |
| 临时源码验收预览 | `127.0.0.1:5001` | Agent 在 Windows 项目目录启动 |
| WSL 交叉预览 | WSL 可达的本地转发 `:5001` | Agent；仅在同一 Windows 仓库上交叉验证 |

权威目录：`本地 Windows 仓库根目录`。`config.json` 默认值不代表预览端口。

### Windows PowerShell 启动源码预览示例

在项目根目录执行，确认 `.venv-win` 存在且依赖已安装。务必把数据写入本次独立临时目录，不要让预览连接正式业务库：

```powershell
$previewData = Join-Path $env:TEMP ("simple-erp-preview-" + [guid]::NewGuid().ToString("N"))
$env:ERP_DATA_ROOT = $previewData
$env:PYTHONPATH = 'app'
.venv-win\Scripts\python.exe -c "from erp import create_app; create_app().run(host='127.0.0.1', port=5001, debug=False, use_reloader=False)"
```

服务停止后，只清理 `$previewData` 指向的本次临时目录。

### WSL 同仓库可选预览

```bash
cd '/mnt/d/<实际仓库父目录>/<仓库目录名>'
preview_data="$(mktemp -d)"
ERP_DATA_ROOT="$preview_data" ERP_PORT=5001 PYTHONPATH=app .venv/bin/python app/app.py
```

`app/app.py` 从 `config.json` 读取 host（默认 `127.0.0.1`），`ERP_PORT` 覆盖端口。WSL Python 环境、进程和 Windows 浏览器可达性必须现场核实；不能只因命令写在文档里就宣称服务已运行。除非经过安全确认，不要改绑到 `0.0.0.0`；当前产品永久免登录。

### Git worktree

- worktree 常没有独立 venv；可复用主仓库环境，但源码进程的当前工作目录必须指向正在验收的 worktree。
- 不要把旧的 `ERP系统-wt-*` 示例路径复制到当前项目。使用当前已确认的 worktree 绝对路径，并核对进程 cwd、分支和响应内容。
- 测试命令必须在目标 worktree 运行；main 上的测试结果不能代表未合并分支。

## 启动后的验收门

1. 核对端口监听 PID、进程启动参数和当前工作目录。
2. 请求 `http://127.0.0.1:5001/health`，检查状态、版本与 `auth: disabled`。
3. 请求本次改动涉及的页面，核对独特的功能标记与真实行为；`/health` 单独通过不代表页面正确。
4. 浏览器页面与服务重启后再验证；`debug=False` 时 Python 路由改动需重启。
5. 桌面 EXE、浏览器和 WSL 服务是不同验收对象，不能互相代替。
6. 回答中给可点击 Markdown 链接，并附纯 URL，避免客户端把链接格式拼坏。

当前版本永久免登录，不需要 cookie 才能访问业务页面。免登录不代表可开放到局域网；预览默认仅绑定回环地址。打印页/销售单 PDF 与普通页面的用户体验不同，按对应任务验收。
