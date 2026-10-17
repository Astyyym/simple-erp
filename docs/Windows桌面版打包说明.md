# Windows 桌面版打包说明

目标：生成用户可直接双击运行的 Windows 桌面版，不要求日常使用者打开终端、安装 Python 或连接互联网。

当前有三种交付形态（一条命令 `打包Windows安装包.bat` 出齐）：

| 形态 | 产物 | 适用 | 演示数据 |
|---|---|---|---|
| **安装包**（推荐给门店） | `dist\simple-erp-setup-vX.Y.Z.exe` | 双击安装到 `%LOCALAPPDATA%\Programs\简单ERP`，开始菜单/桌面图标，可正常卸载；per-user，不需要管理员 | 无 |
| 交付 ZIP | `dist\simple-erp-windows-vX.Y.Z.zip` | 发给别人，解压即用 | 无 |
| 绿色包（免安装） | `dist\local-vX.Y.Z\简单ERP\` | 本机/U 盘直接跑，**自带 200 条演示数据**（包内 `demo_data\`），打开即可预览 | **200 条** |

**安装包与 ZIP 是同一份 PyInstaller one-folder 产物**（正常入口 `简单ERP.spec`，从 `build\clean-vX.Y.Z` 构建），安装包只是在其之上加了一层安装器。**绿色包是另一份构建**（演示入口 `简单ERP-demo.spec` → `packaging/demo_entry.py`，把数据根强制指向包内 `demo_data\`，与「文档\简单ERP数据」物理隔离），最后由 `packaging/seed_demo_data.py` 写入 200 条演示数据。两者不得混用：安装包与 ZIP 里不得出现 `demo_data`（`packaging/make_release_zip.py` 会在写盘前拒绝）。

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

### 2.1 一次出齐三种产物（发版用）

```bat
打包Windows安装包.bat
```

脚本按固定顺序走五步：① 正常入口 one-folder → `build\clean-<版本>\简单ERP`；② Inno Setup（显式 `/DSourceDir=<干净产物>`）→ `dist\simple-erp-setup-<版本>.exe`；③ `packaging\make_release_zip.py` → `dist\simple-erp-windows-<版本>.zip`；④ 演示入口 one-folder → `dist\local-<版本>\简单ERP`；⑤ `packaging\seed_demo_data.py --documents 200` 写入演示数据。

顺序是硬约束：写演示数据必须是最后一步（ZIP 早于它生成，两道闸都防「安装包混进演示数据」）。每轮打包前脚本会先删掉上一轮的 `build\clean-*` 与 `dist\local-*`——残留的 `demo_data` 会让 seed 以「目标库已存在」拒绝执行。

需要 Inno Setup 6（只在本机构建时需要，不进交付包）：

```bat
winget install --id JRSoftware.InnoSetup
```

安装脚本 `packaging\installer\简单ERP.iss` 的硬约束（由 `tests/test_installer_packaging.py` 守护）：

- **per-user 安装**：`DefaultDirName={localappdata}\Programs\简单ERP`、`PrivilegesRequired=lowest`、禁用提权覆盖。**不得改成 Program Files**——程序把可写 `config.json` 放在 EXE 旁（`erp/config.py: runtime_root()`），装进受保护系统目录会让非管理员保存设置失败。
- **升级不覆盖店铺设置**：`config.json` 单独一条 `onlyifdoesntexist uninsneveruninstall`。
- **不碰业务数据**：`[Files]`/`[UninstallDelete]` 只覆盖 `{app}`；不打包 `*.db`/演示库/日志。
- **版本单一真源**：ISPP 从仓库根 `VERSION` 读，脚本内不写死版本号。
- 简体中文安装界面需要 `packaging\installer\ChineseSimplified.isl`（Inno 发行包不带中文语言文件）。

#### 安装 / 升级 / 卸载实测记录（v4.3.0，2026-10-17）

在本机真实安装（自选目录 `D:\app\简单ERP`）上按门店路径走了一遍，证据如下：

| 动作 | 实测结果 |
|---|---|
| 同版本静默覆盖安装（`/SILENT /SUPPRESSMSGBOXES /NORESTART`） | 退出码 0；程序文件**真重写**（安装日志 `Installing the file.`）；`config.json` 逐字节不变（日志 `Dest file exists. Skipping due to "onlyifdoesntexist" flag.`）；`data\erp.db` 与 `data\mobile_devices.json` 逐字节不变 |
| 覆盖安装后启动 | `/health` = `{"status":"ok","version":"v4.3.0","database":"ok","data_root":"C:\Users\<用户>\Documents\简单ERP数据"}`；`127.0.0.1:5000` 200；局域网 `5001 /m/` → 302 到 `/m/pair`；蓝图外 `/customers/` 404 |
| 卸载（`unins000.exe /VERYSILENT /SUPPRESSMSGBOXES`） | 退出码 0；`{app}` 只剩 `config.json`（`uninsneveruninstall` 的预期，日志 `Failed to delete directory (145)` = 目录非空）；开始菜单项与桌面快捷方式删除；`HKCU\...\Uninstall\{8F1C2A64-...}_is1` 删除；`data\erp.db`、`mobile_devices.json` 逐字节不变 |

**安装目录记忆（已修复，待随下一版发布）**：Inno 把「上次安装目录」存在卸载注册表键里，卸载会连键一起删，
所以原本**「卸载 → 重装」会静默回到 `DefaultDirName`**（v4.3.0 实测：装到 `D:\app\简单ERP`，卸载后重装
落到了 `%LOCALAPPDATA%\Programs\简单ERP`）并在新目录生成一份**全默认 `config.json`**——对装到
D 盘的门店用户表现为「店铺名 / 打印偏移 / 主题 / 手机开关全部回到默认」，而原件其实仍在旧目录。

现在 `[Code]` 在 `ssPostInstall` 里把 `{app}` 写进 `HKCU\Software\简单ERP\LastAppDir`（这个键不会被
卸载删除），`DefaultDirName={code:GetLastAppDir}` 读它；读不到才回退 `{localappdata}\Programs\简单ERP`。
`tests/test_installer_packaging.py::test_installer_remembers_previous_install_dir_across_uninstall` 守死这四点。

实测（2026-10-17，用**修复后的 iss + v4.3.0 程序产物**编出的测试包，装在 `D:\app\简单ERP-test`）：

| 场景 | 结果 |
|---|---|
| 装到非默认目录 | exit 0；`LastAppDir` = `D:\app\简单ERP-test` |
| 卸载 | exit 0；卸载键删除，**`LastAppDir` 保留** |
| 不带 `/DIR` 重装 | **落回 `D:\app\简单ERP-test`**；`config.json` 逐字节不变（`065e90c9…`）；默认目录未被创建 |
| 已安装状态下覆盖安装（升级路径） | 落点不变，`config.json` 不变 |
| 删掉注册表键后全新安装 | 正确落到 `{localappdata}\Programs\简单ERP`，并写入 `LastAppDir` |
| 真实安装 `D:\app\简单ERP` | 全程未被测试触碰（`config.json` 仍 `bee3fcbb…`，5 个条目） |

**v4.3.0 已发布，安装包字节不能改，此修复只能随下一版交付**；在那之前，用户遇到「卸载后重装」时
手动选回原安装目录即可（`/DIR=<原目录>` 同理，`config.json` 会被原样复用）。

升级前用户的配置在 `config.json`（EXE 旁），业务数据在数据根；「升级不覆盖配置 / 卸载不删数据」
两条契约本次均已逐字节验证通过。

### 2.2 绿色包（免安装、自带 200 条演示数据）

绿色包由上面的发版脚本第 ④⑤ 步生成（`dist\local-vX.Y.Z\简单ERP\`）。旧脚本 `打包Windows桌面版.bat` 只出普通绿色目录（不含演示数据、不发版用）。

```text
dist\local-vX.Y.Z\简单ERP\
  简单ERP.exe
  config.json
  demo_data\            ← 200 条演示数据（仅绿色版有）
    data\erp.db
  _internal\
```

绿色包验收要点：包内 `demo_data\data\erp.db` 存在、四类单据**全表**计数 = 200（`orders` + `purchase_orders` + `purchase_return_orders`，只数 `orders` 会得到 125 而误判）、`PRAGMA integrity_check` = ok、包根配置是演示店铺名；真实启动后 `/health` 的 `data_root` 指向包内 `demo_data`。安装包与 ZIP 里 `*.db` / `demo_data` 必须为 **0 个**，且 ZIP 内 EXE 与干净产物 EXE 同哈希；两份 EXE 哈希必须不同（证明入口确实分开）。

打包脚本也可能生成版本化 ZIP。实际文件名及版本以本次打包脚本输出为准，常见示例：

```text
dist\simple-erp-setup-vX.Y.Z.exe
dist\simple-erp-windows-vX.Y.Z.zip
```

仅本地存在 ZIP/EXE 不能证明其已上传 GitHub Releases；推送源码、构建包和发布 Release 是不同状态。

本轮 `v2.0.0` 为独立新目录 `dist\local-v2.0.0\简单ERP\` 和 `dist\simple-erp-windows-v2.0.0.zip`。由当时Windows源码fresh构建，未覆盖上述常规目录或旧包。ZIP完整解压后使用；根配置与冻结内置配置都来自 `packaging/default_config.json`，不是本机config。Windows产品版本保留精确 `v2.0.0`，数字文件版本为 `2.0.0.0`。

当前 `v3.15.0` 在 v3.14.0 之上交付**商品售价来源收敛（默认价下线）与客户专属价查看**：商品档案不再有「默认价」，售价唯一来源 = 该客户的专属价（有专属价自动带出，没有则留空手输，手输价大于 0 自动学成专属价、0 元不学；换客户按新客户重取，没有则清空）；销售 / 退货页的「常规价」勾选与拿货 / 退拿货页的「常规价」按钮删除；商品列表「启用状态」列与编辑页只读格删除（库存数量 / 成本 / 期初 / 告急 / 利润分析 / 分析中心保留）；商品管理列表新增「专属价」列（「查看专属价格」入口）、「编辑」按钮改名「查看详情」，商品编辑页新增「客户专属价」卡片。上一版 `v3.14.0` 交付**开单单价随客户走 + 退货来源候选放开成本门槛**（已推送 `main` 并发布 Release `v3.14.0`）。产物为 `dist\local-v3.15.0\简单ERP\`（**自带 200 条演示数据**）、`dist\simple-erp-setup-v3.15.0.exe`、`dist\simple-erp-windows-v3.15.0.zip`（后两者不含演示数据）。Windows 产品版本 `v3.15.0`，数字文件版本 `3.15.0.0`。本版已推送 `main`（提交 `fa0b582`）并发布 Release `v3.15.0`（双资产下载回本地与构建产物字节级一致）；安装包真实安装/升级/卸载链路与目标打印机实打仍未验。

此前 `v3.12.0` 在 v3.11.2 之上交付**关联带入可替换、检索串归一、档案筛选与流水展开、编辑页主次重构**：换掉关联原单时只撤系统带入的行（用户手输行不动）、输入法隔音符号 / 空格 / 全角检索串先归一、商品管理恢复「全部 / 待补全 / 按拼音排序」、导入字段不完整不再拦行改标「待补全」、账款流水可展开单据明细、商品 / 客户编辑页改为主栏 + 320px 侧栏。产物为 `dist\local-v3.12.0\简单ERP\`（**自带 200 条演示数据**）、`dist\simple-erp-setup-v3.12.0.exe`、`dist\simple-erp-windows-v3.12.0.zip`（后两者不含演示数据）。Windows 产品版本 `v3.12.0`，数字文件版本 `3.12.0.0`。v3.11.2 修掉「按预览带入商品行点几次插几次」并把拼音扩到整串全拼；v3.11.1 修掉点选商品后下拉不关 / 单位与单价不带入，并加入每联打印行数 6/8/10/12 可配置。

当前 `v3.10.0` 在 v3.9.1 之上交付**单位落库语义统一 + 单位记忆 + 联想下拉去 datalist**（本版为行为变化，故抬 minor）：修掉「选中已有商品时提交的单位被静默丢弃」、拿货/退拿货页单位改可编辑（只写本单明细不回写档案）、退货/退拿货来源带入默认沿用源单单位且允许修改、四页单位下拉改用候选池（零迁移）、三张列表页与开单页的联想全部换成应用自有浮层（原生 `datalist` 在 WebView2 不绘制）、新增客户侧拼音字段。产物为 `dist\local-v3.10.0\简单ERP\`、`dist\simple-erp-setup-v3.10.0.exe` 和 `dist\simple-erp-windows-v3.10.0.zip`。Windows 产品版本 `v3.10.0`，数字文件版本 `3.10.0.0`。

当前 `v3.9.1` 在 v3.9.0 之上修复**桌面壳端口写死 5000**：端口原先在 `desktop_app.py` 的 `APP_URL`/`_run_server`/`_port_is_open` 三处硬编码，既不读 `config.json` 的 `port` 也不读 `ERP_PORT`——无法在别的端口跑隔离实例。现按 `ERP_PORT` > `config.json` 的 `port` > 5000 解析（与源码入口 `app/app.py` 同口径）；**host 仍固定 `127.0.0.1`**（产品永久免登录，配置写 `0.0.0.0` 也不放行）。产物为 `dist\local-v3.9.1\简单ERP\`、`dist\simple-erp-setup-v3.9.1.exe` 和 `dist\simple-erp-windows-v3.9.1.zip`。Windows 产品版本 `v3.9.1`，数字文件版本 `3.9.1.0`。

当前 `v3.9.0` 在 v3.8.0 之上交付**历史成本人工补录（销售单）**：零建档/先卖后补路径下的老销售单可人工填成本、系统补快照并留痕（**只补利润、不动库存**，库存三表逐行不变）；新增 `order_items.cost_source` 标记（真实快照不可覆盖 / `backfill` 可再修正，幂等加列、不升 schema 版本）；补录后该单禁止重编辑，已有有效退货时拒绝再修正。产物为 `dist\local-v3.9.0\简单ERP\`、`dist\simple-erp-setup-v3.9.0.exe` 和 `dist\simple-erp-windows-v3.9.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.9.0`，数字文件版本 `3.9.0.0`。**用户侧人工验收（2026-10-12）**：真实门店数据上的补录流程已走通；目标打印机实打（10 行单 / 11 行单）已完成。

当前 `v3.8.0` 在 v3.7.0 之上交付**单联 10 行续打、开单行工具条、一键清空数据与打印渲染提速**：单联容量 8 → **10 行**（压单元格内边距 1.6→0.9mm，字号不动），非末联给「本张合计」+ 续打提示行并强制换联、末联给「本单合计」+ 页脚；四类开单表格下方新增 `[新增一行] [删除一行] [一键清空]` 工具条；设置页新增「清空所有数据」（保留 `backups/`+`legacy_archive/`+数据目录记忆+库文件，保留当日 MD 流水高位，清前自动 `pre_purge` 备份 + 输确认词）；「保存并打印」PDF 渲染 **2 次 → 1 次**（1.9s → 0.822s）。产物为 `dist\local-v3.8.0\简单ERP\`、`dist\simple-erp-setup-v3.8.0.exe` 和 `dist\simple-erp-windows-v3.8.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.8.0`，数字文件版本 `3.8.0.0`。**目标打印机实打（10 行单 / 11 行单）已完成**（用户侧人工验收，2026-10-12 确认）。

当前 `v3.7.0` 在 v3.6.0 之上修复**四类开单连续纸打印**：字号偏小（正文字框高 3.84→4.89mm，对齐门店旧软件实拍水平）与版式回归（恢复秒账式 241mm 连续纸模型，逻辑页 215.9×139.7mm、页内左右各 7.3mm 版心、正文约 201.3mm），并正式分离两套纸型（四类开单 = 241mm 连续纸；客户货款汇总 / 账款流水 / 往来对账 = A4 竖版）。产物为 `dist\local-v3.7.0\简单ERP\`、`dist\simple-erp-setup-v3.7.0.exe` 和 `dist\simple-erp-windows-v3.7.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.7.0`，数字文件版本 `3.7.0.0`。**未验**：目标打印机实打（`NantianPR-LQ` 纸面位置仍须实机确认）。

当前 `v3.6.0` 在 v3.5.0 之上新增**安装包形态**：产物为 `dist\local-v3.6.0\简单ERP\`、`dist\simple-erp-setup-v3.6.0.exe` 和 `dist\simple-erp-windows-v3.6.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。安装包为 per-user 安装（`%LOCALAPPDATA%\Programs\简单ERP`），不需要管理员；`config.json` 升级不覆盖、卸载保留；业务数据目录零接触。Windows 产品版本 `v3.6.0`，数字文件版本 `3.6.0.0`。

当前 `v3.5.0` 在 v3.4.0 之上交付「筛选无刷新、侧栏导航归属、动作标签统一与数据分析图型收口」整批（六页筛选/翻页就地刷新不再闪烁回顶 + 共享 `page-refresh.mjs`、侧栏高亮改按路由 owner 判定、查看/编辑/打印词统一、条件不可用的「编辑」改置灰+原因、成本环形切换与柱状生长动画），产物为 `dist\local-v3.5.0\简单ERP\` 和 `dist\simple-erp-windows-v3.5.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.5.0`，数字文件版本 `3.5.0.0`。本版已推送 GitHub 并发布 Release `v3.5.0`（ZIP 资产下载回本地与构建产物字节级一致）；桌面 EXE 原生窗口/OS 鼠标矩阵、实体打印与浅/深/跟随系统三主题矩阵仍未验。

当前 `v3.4.0` 在 v3.3.0 之上交付「EXE 启动速度优化」整批（惰性导入 `weasyprint`/`openpyxl`/`pypinyin`/`PIL` + 桌面壳品牌 splash 先行与抢跑修复），产物为 `dist\local-v3.4.0\简单ERP\` 和 `dist\simple-erp-windows-v3.4.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.4.0`，数字文件版本 `3.4.0.0`。本版已推送 GitHub 并发布 Release `v3.4.0`（ZIP 资产下载回本地与构建产物字节级一致）；真实桌面观感（splash 无闪烁/重复双击）待用户实机确认。

当前 `v3.3.0` 在 v3.2.0 之上交付「旧数据迁移承接」整批（备份与恢复、导入扩列与两段式预检、期初成本来源、区间两端余额、档案来源标记与零建档起步、店铺信息引导与顶栏实时钟、基础资料列表页三段式重排、品牌区英文副标题移除），产物为 `dist\local-v3.3.0\简单ERP\` 和 `dist\simple-erp-windows-v3.3.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.3.0`，数字文件版本 `3.3.0.0`。

当前 `v3.2.0` 在 v3.1.0 之上合并交付产品审查修复第二轮与筛选卡三段式重排，产物为 `dist\local-v3.2.0\简单ERP\` 和 `dist\simple-erp-windows-v3.2.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.2.0`，数字文件版本 `3.2.0.0`。

当前 `v3.1.0` 为首次推送 `main` 并创建 GitHub Release 的版本，产物为 `dist\local-v3.1.0\简单ERP\` 和 `dist\simple-erp-windows-v3.1.0.zip`，同样 fresh 构建、只含通用配置、不携带业务/演示/测试数据。Windows 产品版本 `v3.1.0`，数字文件版本 `3.1.0.0`。

## 3. 安装与启动

**安装包**：双击 `simple-erp-setup-vX.Y.Z.exe`，按向导下一步即可（安装前可读「安装说明」页）。程序装到 `%LOCALAPPDATA%\Programs\简单ERP`，开始菜单出现「简单ERP」，勾选后桌面也有快捷方式；卸载走「设置 → 应用」或开始菜单的「卸载 简单ERP」。

**绿色包**：将整个 `dist\local-vX.Y.Z\简单ERP` 文件夹复制到目标 Windows 电脑，例如 `D:\简单ERP\`，然后创建 `简单ERP.exe` 的快捷方式。不要只复制 EXE，one-folder 依赖 `_internal`。

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

**用安装包升级**：直接运行新版本安装包即可，不必先卸载。安装器会覆盖程序文件，但不会覆盖用户改过的 `config.json`（店铺名、打印偏移、主题等），也不会碰业务数据目录。卸载同样只删程序文件。

如果旧安装把数据放在 EXE 旁边的 `data\`，整目录覆盖风险更高；应先确认实际数据根目录。绿色包升级不需要通过卸载程序完成。

`v2.0.0` 首次连接旧库会执行内置schema升级，升级前需备份整个数据根并保留旧程序的config.json。通用默认配置不能覆盖旧店铺/打印设置；旧历史库存/成本不自动补造，正式期初启用需独立确认，不能升级后直接用旧程序打开新库。本轮只在隔离虚构与空数据上验证，不迁移或投用正式库。

## 6. 打印

- 销售单、退货单、客户货款汇总 PDF 和设置页样张默认使用 A4 竖版（210×297mm）；销售/退货单内容为正向竖版排版，不再放入旧 241×140mm 单据区。
- 打印驱动建议选 A4 纵向、实际大小/100%，关闭“适合页面/缩放”。历史 `NantianPR-LQ` 记录曾提供 A4 纸型，但当前驱动版本和目标电脑选项未重新核验。
- 设置页的横纵偏移和缩放校准销售/客户退货、拿货/退拿货单及设置样张；账款汇总和往来交易对账 PDF 不使用这些偏移。
- 先检查 HTML/PDF 预览，再在目标电脑/打印机实打校准。生成 PDF 或自动化测试通过不能替代目标打印机人工验收。

## 7. 证据与发布状态

每次正式交付分别记录：

1. 测试与源码运行结果；
2. Windows EXE 是否从隔离目录真实启动、`/health` 版本是否正确、业务页是否可用；
3. ZIP 是否正确生成及其文件名/大小；
4. Git 是否提交/推送；
5. GitHub Release 是否存在且包含预期资产。

不得把其中一项推断成其他项已完成。打包流程和 Windows 中文路径冒烟细节见项目技能 references 索引。

`v2.0.0` 本轮证据：529项Python/24项前端、fresh EXE持有回环5000且health精确版本与隔离根、21组分析交互/主题宽度、7组HTTP与6份逐页A4 PDF、原生WebView2真实Python桥及9组路径初启/重启、随包通用配置空库初始化与样张通过。PDF关键GTK/Pango模块从随包_internal加载；ZIP逐文件SHA256/CRC及保护元数据/进程/端口清理通过。原生窗口完整视觉/OS鼠标矩阵、保存对话框人工选择/取消、目标打印机、独立新电脑/完全断网环境仍未验，不据此宣称全平台或离线可靠。Git提交、推送、tag、Release均未执行。
