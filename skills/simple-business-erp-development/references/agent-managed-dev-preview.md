# Agent-managed development preview

适用于需要在 Windows 浏览器验收源码页面的 ERP 迭代。只有预览服务真实启动并确认用户访问的是正确实例后，才提供预览链接。

## ⚠️ 隔离环境变量名：只有 `ERP_DATA_ROOT` 生效

写探针/播种/验收脚本时，**必须**用 `ERP_DATA_ROOT`（`app/erp/config.py:122`）。曾经用过的 `ERP_DATA_DIR` 是**错的**，会被静默忽略：`resolve_data_root()` 读不到该变量，就一路回落到「路径记忆 → 默认数据根」，**把测试数据写进用户的真实数据根**。

正确的完整隔离三件套（缺一会漏）：

```python
os.environ["ERP_DATA_ROOT"] = str(DATA)          # 业务库所在根（唯一决定库位置的变量）
os.environ["ERP_CONFIG_PATH"] = str(DATA / "config.json")
os.environ["ERP_LOCATION_FILE"] = str(DATA / "location" / "data_location.json")
```

**写完数据后必须回读确认真的隔离了，不能假设：**

```python
# 1) 让 app 自己报告它解析到的根（最可靠，不靠推断）
client.get("/health").get_json()["data_root"] == str(DATA)
# 2) 确认真实数据根没被动过（比对 mtime / 行数 / 直接断言表为空）
```

`/health` 会返回实际生效的 `data_root`——**这是判断隔离是否成功的唯一权威依据**。变量名写错时脚本不会报错、`init_db()` 照样成功，只有 `/health` 或事后检查真实库才能发现。

**报告前必须核对用户数据根未被写入。** 一旦污染：不要静默删除，先备份整个数据根（校验文件清单 + `integrity_check`），再让用户决定清理范围（精确删探针行 / 整根重置）。

### 排查污染来源的顺序

用户说「exe/包里怎么有测试数据」时，先分清三层，不要一上来怀疑构建：

1. **交付包内容**：解压 ZIP 列出条目，查 `.db`/`.log`/`data/` 条目；再在 exe 二进制和整个产物目录里按 UTF-8 **与 UTF-16** 双编码搜业务字符串。包干净 → 不是打包问题。
2. **数据根解析**：冻结版启动会读 `%LOCALAPPDATA%\简单ERP\data_location.json` 的路径记忆。**包干净但界面有数据，几乎总是数据根里有旧数据**，不是包被污染。
3. **谁写进去的**：查该库的 `audit_logs`（含精确 `created_at` 和 `summary`），再回 scratch 目录 grep 那些探针名字，定位到具体脚本及其环境变量写法。

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
4. 浏览器页面与服务重启后再验证；`debug=False` 时 Python 路由改动需重启，Jinja 模板也可能继续使用缓存。模板改动后先比对 HTTP 实际响应里的独特样式/标记与当前源码，再操作页面；不能只刷新浏览器或看健康检查。临时源码验收要热加载模板时，在 `app.run(...)` 前显式设 `app.config['TEMPLATES_AUTO_RELOAD'] = True`；仅设置 `app.jinja_env.auto_reload` 可能被 `app.run(debug=False)` 的 debug setter 重置，不作为可靠启用方式。
5. 桌面 EXE、浏览器和 WSL 服务是不同验收对象，不能互相代替。
6. 回答中给可点击 Markdown 链接，并附纯 URL，避免客户端把链接格式拼坏。

当前版本永久免登录，不需要 cookie 才能访问业务页面。免登录不代表可开放到局域网；预览默认仅绑定回环地址。打印页/销售单 PDF 与普通页面的用户体验不同，按对应任务验收。

## Windows Edge/CDP PDF 下载验收

- 给 Chromium 的 `Browser.setDownloadBehavior.downloadPath` 传 Windows 原生反斜杠绝对路径，并使用独立 scratch 下载目录；不能因 Node/Python 接受 `C:/...` 就推定浏览器下载实现也接受同一形式。下载中断时先核对下载记录的实际目标及中断状态，再判断应用是否有问题。
- 触发下载时使用真实点击或 CDP `Runtime.evaluate` 的 `userGesture: true`，等待目标文件真正完成，再核对 `%PDF`、A4 页框、字节/哈希。页面显示「已请求下载」不是文件落盘的证据。
- 每次验收使用新的下载目录，或确认目标文件本轮新建，避免复用同名单号旧 PDF 冒充下载成功。只检查本次隔离浏览器的文件，不读取用户日常浏览历史。
- 未保存的验收表单可能留下 `beforeunload` 确认，使下一次导航或重新附加 CDP 卡住。只对已核实的隔离测试页显式放行导航，或关闭并重建该隔离 target；不关闭用户真实窗口，也不把自动化导航卡住直接诊断为业务/渲染故障。
